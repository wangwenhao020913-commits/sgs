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

**票 13（界顾雍移动版【秉壹】）**：同一张武将牌的第二条技能落地——与 OL 顾雍【秉壹】只差
**条件**一处（「颜色**或类别**均相同」），故与标准版**共用同一个手写主体**、只参数化判据；
座位多选面就是 W1 已落地的那一张（票 02 / 票 09 的断言住在 ``test_hero_yijiang.py``）。

**牌堆控制**：``Engine.from_state`` 的局面从**回合开头**跑起（准备 → 判定 → 摸牌 → 出牌），
故本文件的夹具把 2 张摸牌阶段要摸的牌放在牌堆头、要控制的牌（判定牌 / 慎行的摸牌）放在其后。
"""

from __future__ import annotations

from sgs.data import (
    Color,
    Identity,
    Kingdom,
    RuleConfig,
    Suit,
    card_category,
    color_of_suit,
    load_content_pack,
)
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    Phase,
    PlayCard,
    QueryKind,
    SelectCards,
    SelectOptions,
    SubstituteJudgeCard,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support import (
    RecordingPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_queries,
)

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
        "name": "选择当作哪一张基本牌使用（此刻用不了的名字置灰、不可选）",
        "seat": "选择这张牌的目标（目标由牌名决定时不出现这一步）",
    }, "票 18：三步文案（第 3 步在目标由牌名决定时整条不出现）"


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


def test_jie_jianying_activation_ask_is_unchanged_by_the_interface_default():
    """票 15 只改**界面默认**：服务端询问仍是裸的 [ActivateSkill, Pass]，没有默认候选字段。

    投影侧的 ``default_choice`` 是展示层事实（不进记录流、不进 ``Query``）：③ 的可选性与
    ④ 的规则语义都不因"提交 = 同意"而改变——放弃永远还在候选里。
    """
    _, engine = _jie_ready(hand=(HEART_BOW, 9))

    _jie_use_and_settle(engine, HEART_BOW)
    _jie_use_and_settle(engine, 9)

    asked = _jie_asked(engine, JIE_JIANYING)
    assert len(asked) == 1, "同花色 ⇒ 问一次"
    query = asked[0]
    assert [action.kind for action in query.legal_actions] == ["ActivateSkill", "Pass"], (
        "① 的询问仍是「发动 / 放弃」两条裸候选：界面默认不改服务端可选性"
    )
    assert "default_choice" not in query.model_dump(), (
        "默认候选是投影字段，不长在询问模型上（记录流逐字节不变）"
    )


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
# §2 渐营 ② 接入**全部**「需要 / 可以使用或打出」的窗口（yijiang-2014-round2 票 17 / P8）
#
# 用户 2026-11-09 逐字签的窗口范围：「任何"需要"、"可以使用或打出的场合，例如贾诩乱武也算」。
# 两条行为裁定（同一天签 A）：
#
# - **Q17-b = A**：「出牌阶段限一次」只数**自己出牌阶段内的发动**（``state.turn_seat ==
#   owner`` ∧ 当前阶段 = 出牌阶段）。是 ⇒ 照旧查 / 记 ``_USED`` 阶段位；**否** ⇒ 不查也
#   不记，回合外可以多次发动。**自己的**出牌阶段里因响应窗口而发动（例如自己造成伤害触发
#   的濒死求桃）按字面**算**额度——别写成"窗口一律不占"。
# - **Q17-c = A**：产物花色照旧（"本阶段使用的上一张牌"的花色），但"本阶段"只在**自己的
#   出牌阶段**里可读；回合外 ⇒ 继承来源牌（与"上一张无花色"同一支），**不读**上一次自己的
#   出牌阶段留下的陈旧值。
#
# 窗口面 = 票 16 登记表里的 ``providers=True`` 行：``dodge``（【闪】步 / 万箭 / 护驾）、
# ``peach``（求桃）、``respond`` 一族（南蛮 / 决斗 / 借刀 / 乱武 / 激将）。改判不参与。
# 窗口里**牌名已被窗口定死**（``ctx.respond_required``）⇒ 只剩"选哪张源牌"一步；交互 =
# 点技能 → 选牌 → 提交，产物交给窗口（**不**自己 ``start_use``）。
# ===========================================================================

JIAXU = "jiaxu_mobile"
LUANWU = "hero.jiaxu_mobile.luanwu"
HUJIA = "hero.caocao.hujia"
GUIDAO = "hero.zhangjiao_mobile.guidao"

JIE_JIANYING_WINDOWS = "JIE_JIANYING_PROVIDER_WINDOWS"


class _WindowSeat:
    """窗口脚本：按"这一问我能做什么"应答，其余退到最小合法应答（放弃 / 结束阶段）。

    每个开关**只用一次**（用掉即关）：一份脚本 = "这个座位已经做过的那些决定"，重复使用会
    与"每提供者只问一次"的窗口纪律冲突。``asked`` 录下本座位收到的每条询问（断言窗口的
    候选面 / 推进次序用），断的是**真实询问**，不 mock 引擎。
    """

    def __init__(
        self,
        *,
        use: int | None = None,
        use_at: int | None = None,
        designation: tuple[int, int] | None = None,
        activate: str | None = None,
        provider: bool = False,
        lord: str | None = None,
        source: int | None = None,
        real: int | None = None,
    ) -> None:
        self.use = use
        self.use_at = use_at
        self.designation = designation
        self.activate = activate
        self.provider = provider
        self.lord = lord
        self.source = source
        self.real = real
        self.asked: list = []

    def respond(self, query):
        self.asked.append(query)
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "SKILL_ACTIVATION":
            # ① 的「是否发动」：这些用例都不摸牌（摸了会让手牌读数多一个变量）。
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if kind == "PLAY_PHASE":
            if self.designation is not None:
                holder, bn = self.designation
                for action in getattr(query, "designation_actions", ()):
                    if (
                        isinstance(action, UseCard)
                        and action.targets == (holder,)
                        and action.designations == (bn,)
                    ):
                        self.designation = None
                        return action
            if self.activate is not None:
                for action in query.legal_actions:
                    if (
                        isinstance(action, ActivateSkill)
                        and action.skill_id == self.activate
                    ):
                        self.activate = None
                        return action
            if self.use is not None:
                for action in query.legal_actions:
                    if (
                        isinstance(action, UseCard)
                        and action.card.card_id == self.use
                        and (self.use_at is None or action.targets == (self.use_at,))
                    ):
                        self.use = None
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if kind in ("DODGE", "RESPOND", "PEACH"):
            if self.lord is not None:
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill) and action.skill_id == self.lord:
                        self.lord = None
                        return action
            if self.provider:
                for action in query.legal_actions:
                    if (
                        isinstance(action, ActivateSkill)
                        and action.skill_id == JIE_JIANYING
                    ):
                        self.provider = False
                        return action
            if self.real is not None:
                for action in query.legal_actions:
                    if (
                        isinstance(action, (UseCard, PlayCard))
                        and action.card.card_id == self.real
                    ):
                        self.real = None
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if kind == "SELECT_CARDS":
            # 窗口里的那一步 = "选哪张源牌"；池由服务端给（手牌 ∪ 装备区），脚本只点一张。
            if self.source is not None:
                for action in query.legal_actions:
                    if not isinstance(action, SelectCards) or len(action.picks) != 1:
                        continue
                    slot = query.slots[action.picks[0]]
                    card = getattr(slot, "card", None)
                    if card is not None and card.card_id == self.source:
                        self.source = None
                        return action
            return query.legal_actions[0]
        for action in query.legal_actions:
            if isinstance(action, (EndPhase, Pass)):
                return action
        return query.legal_actions[0]


def _jie_window_state(*, seats, turn_seat: int = 0, deck=None):
    """窗口用例的公共局面：``turn_seat`` 从**回合开头**跑起（准备 → 判定 → 摸牌 → 出牌）。"""
    used = tuple(card for spec in seats for card in (*spec.hand, *spec.equip))
    return build_state(
        pack=PACK,
        seats=list(seats),
        deck=deck if deck is not None else deck_excluding(PACK, *used),
        turn_seat=turn_seat,
        phase=Phase.PLAY,
        # 2 人局 = 无身份局：先手就是 turn_seat。4 人以上 = 身份局，先手由主公座位定
        # （`first_seat` 不得预设），故只给座位数、`turn_seat` 由构造局面显式给。
        rule_config=(
            RuleConfig(player_count=2, first_seat=turn_seat)
            if len(seats) == 2
            else RuleConfig(player_count=len(seats))
        ),
    )


def _jie_run(state, players, stop):
    """装脚本推进到 ``stop``（窗口用例唯一的推进器；读数全走记录流 / 询问面）。"""
    engine = Engine.from_state(state, seed=7)
    run_queries(engine, players, stop)
    return engine


def _jie_window_offered(engine, cause, required) -> bool:
    """停止条件：``engine.pending`` **正是那一扇窗口**，且 ② 在它的候选里。

    窗口用例先用它停在询问面读"这一问是谁、要什么牌、候选里有没有 ②"，再接着推进到
    结算完——两侧都读真实询问 / 真实记录流，不 mock 窗口。
    """
    pending = engine.pending
    return (
        pending is not None
        and pending.kind == QueryKind.RESPOND
        and getattr(pending, "cause", None) is cause
        and getattr(pending, "required", None) == required
        and any(
            isinstance(action, ActivateSkill) and action.skill_id == JIE_JIANYING
            for action in pending.legal_actions
        )
    )


def _jie_closed(engine, frame_kind) -> bool:
    """记录流里某个帧**已经出栈**（= 那一整段结算跑完）。"""
    return any(
        record.kind == "frame"
        and record.op == "pop"
        and record.frame_kind is frame_kind
        for record in engine.records
    )


def _jie_timing_cards(engine, timing: Timing) -> list:
    """记录流里某时机带的牌面（这一次**使用** / **打出**的那张，含虚拟产物）。"""
    return [
        record.cards[0]
        for record in engine.records
        if record.kind == "timing" and record.timing is timing and record.cards
    ]


def _jie_played_cards(engine) -> list:
    """打出的牌（``PLAY_ON_PLAY`` 的那一张）：产物走的是**打出**链还是使用链，这条读得出。"""
    return _jie_timing_cards(engine, Timing.PLAY_ON_PLAY)


def test_jie_jianying_converts_a_card_into_a_peach_in_the_near_death_window():
    """濒死求【桃】：牌名被窗口定死 ⇒ 选一张源牌就成交，回复由**求桃窗口**建立。"""
    slash = cards_of_type(PACK, "slash")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(slash, CLUB_CROSSBOW)),
            SeatSpec(hp=1, hp_max=4),
        ),
    )
    engine = _jie_run(
        state,
        [_WindowSeat(use=slash, provider=True, source=CLUB_CROSSBOW), _WindowSeat()],
        lambda e: _jie_closed(e, FrameKind.NEAR_DEATH),
    )

    products = [card.type for card in _jie_used_products(engine)]
    assert products == ["peach"], (
        f"挨了一刀之后救命的是一张**转出来的**【桃】（使用链上已结算的牌：{products}）"
    )
    assert state.player(1).hp == 1 and state.player(1).alive, "求桃窗口把座位 1 拉回 1 点"
    assert _jie_moves(engine, CLUB_CROSSBOW) == [
        ("hand", "processing"),
        ("processing", "discard"),
    ], "源牌照常走使用链 ⇒ 进弃牌堆（② 的成本恰好一张）"


def test_jie_jianying_converts_a_card_into_a_slash_for_borrowed_sword():
    """借刀杀人（``mode = USE``）：产物的目标由窗口承载（Bn），技能自己不 ``start_use``。"""
    borrowed = cards_of_type(PACK, "borrowed_sword")[0]
    # 2 人局里 Bn = 那位借刀的使用者自己（距离 1，够得着）；4 人局要先给 Bn 装备 -1 马。
    state = _jie_window_state(
        seats=(
            # 借刀只会指定**装备区里有武器**的角色：座位 0 先挂着【青釭剑】。
            SeatSpec(
                hero=JUSHUO,
                hp=2,
                hp_max=3,
                hand=(CLUB_CROSSBOW,),
                equip=(cards_of_type(PACK, "qinggang_sword")[0],),
            ),
            SeatSpec(hand=(borrowed,)),
        ),
        turn_seat=1,
    )
    engine = _jie_run(
        state,
        [
            _WindowSeat(provider=True, source=CLUB_CROSSBOW),
            _WindowSeat(designation=(0, 1)),
        ],
        lambda e: any(
            record.kind == "timing"
            and record.timing is Timing.RESOLVE_AFTER_END
            and record.cards
            and record.cards[0].type == "slash"
            for record in e.records
        ),
    )

    products = [card for card in _jie_used_products(engine) if card.type == "slash"]
    assert len(products) == 1, "借刀窗口只逼出一张【杀】"
    product = products[0]
    assert product.virtual_sources == (CLUB_CROSSBOW,), "产物来自那张源牌"
    assert state.player(1).hp == 3, "窗口把它打向了 Bn（借刀的使用者掉 1 点）"
    assert _jie_moves(engine, CLUB_CROSSBOW) == [
        ("hand", "processing"),
        ("processing", "discard"),
    ]


def test_jie_jianying_converts_a_card_into_a_slash_to_answer_barbarian_invasion():
    """南蛮入侵（``mode = PLAY``）：产物走**打出**链（规则 §16.2），不进使用链。"""
    barbarian = cards_of_type(PACK, "barbarian_invasion")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(CLUB_CROSSBOW,)),
            SeatSpec(hand=(barbarian,)),
        ),
        turn_seat=1,
    )
    engine = _jie_run(
        state,
        [_WindowSeat(provider=True, source=CLUB_CROSSBOW), _WindowSeat(use=barbarian)],
        lambda e: ran_timing(e, Timing.RESOLVE_AFTER_END),
    )

    played = _jie_played_cards(engine)
    assert [card.type for card in played] == ["slash"], "打出的是一张转出来的【杀】"
    assert played[0].virtual_sources == (CLUB_CROSSBOW,)
    assert [card.type for card in _jie_used_products(engine)] == ["barbarian_invasion"], (
        "产物只打出、不进使用链：使用链上只有南蛮自己"
    )
    assert state.player(0).hp == 2, "响应成立 ⇒ 不受南蛮那 1 点伤害"


def test_jie_jianying_converts_a_card_into_a_dodge_to_answer_arrow_barrage():
    """**万箭齐发**（``mode = PLAY``，要【闪】）：产物走**打出**链，不进使用链。

    票面 §2 表 / spec §11 的 P8 行都点名「南蛮 / 万箭 / 决斗」，而旧断言只覆盖南蛮——
    万箭与决斗这两扇窗口各自单独钉一条，走的是同一个 ``ProviderWindows`` 声明面。
    """
    arrows = cards_of_type(PACK, "arrow_barrage")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(CLUB_CROSSBOW,)),
            SeatSpec(hand=(arrows,)),
        ),
        turn_seat=1,
    )
    players = [
        _WindowSeat(provider=True, source=CLUB_CROSSBOW),
        _WindowSeat(use=arrows, use_at=0),
    ]
    engine = _jie_run(
        state,
        players,
        lambda e: _jie_window_offered(e, WindowCause.ARROW_BARRAGE, "dodge"),
    )

    window = engine.pending
    assert window.actor == 0, "被问的是万箭的目标（渐营持有者），不是使用者"
    assert window.required == "dodge", "万箭齐发要的是【闪】（不是南蛮那张【杀】）"

    run_queries(engine, players, lambda e: _jie_closed(e, FrameKind.USE))

    played = _jie_played_cards(engine)
    assert [card.type for card in played] == ["dodge"], "打出的是一张转出来的【闪】"
    assert played[0].virtual_sources == (CLUB_CROSSBOW,)
    assert [card.type for card in _jie_used_products(engine)] == ["arrow_barrage"], (
        "产物只打出、不进使用链：使用链上只有万箭齐发自己"
    )
    assert state.player(0).hp == 2, "响应成立 ⇒ 不受万箭那 1 点伤害"


def test_jie_jianying_converts_a_card_into_a_slash_to_answer_duel():
    """**决斗**（``mode = PLAY``，要【杀】族）：产物走**打出**链；没接住的一方受伤。

    决斗的响应权在双方之间来回，故这条同时钉住"② 的产物被当**真打出**推进决斗游标"：
    我打出那张【杀】之后，轮到对方，对方没有【杀】⇒ 受伤的是对方。
    """
    duel = cards_of_type(PACK, "duel")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(CLUB_CROSSBOW,)),
            SeatSpec(hand=(duel,)),
        ),
        turn_seat=1,
    )
    players = [
        _WindowSeat(provider=True, source=CLUB_CROSSBOW),
        _WindowSeat(use=duel, use_at=0),
    ]
    engine = _jie_run(
        state,
        players,
        lambda e: _jie_window_offered(e, WindowCause.DUEL, "slash"),
    )

    window = engine.pending
    assert window.actor == 0 and window.target is None, "决斗是**打出**窗口（target 为 None）"
    assert window.required == "slash"

    run_queries(engine, players, lambda e: _jie_closed(e, FrameKind.USE))

    played = _jie_played_cards(engine)
    assert played and played[0].type == "slash", "打出的是一张转出来的【杀】"
    assert played[0].virtual_sources == (CLUB_CROSSBOW,)
    assert [card.type for card in _jie_used_products(engine)] == ["duel"], (
        "产物只打出、不进使用链：使用链上只有决斗自己"
    )
    assert state.player(0).hp == 2, "我接住了那一轮 ⇒ 不受决斗的 1 点伤害"
    assert state.player(1).hp == 3, "对方没接住 ⇒ 决斗的输家是使用者"


def test_jie_jianying_converts_a_card_into_a_dodge_for_the_slash_window():
    """§2 表**之外**的有意读法：语境键 ``dodge`` 同时带来【杀】的【闪】步。

    票面 §2 的【闪】只列在"护驾 / 激将"那一行，但票 16 的 ``ProviderWindows`` **只按语境
    收窄**（`ModifierContext` 不带窗口成因）⇒ 真【杀】的【闪】步与万箭 / 护驾共用同一档。
    这一条把它钉成**真的能用**（不是只进了候选面）：转出来的【闪】走使用链、那张【杀】被
    抵消。
    """
    slash = cards_of_type(PACK, "slash")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(CLUB_CROSSBOW,)),
            SeatSpec(hand=(slash,)),
        ),
        turn_seat=1,
    )
    engine = _jie_run(
        state,
        [
            _WindowSeat(provider=True, source=CLUB_CROSSBOW),
            _WindowSeat(use=slash, use_at=0),
        ],
        lambda e: any(
            record.kind == "timing"
            and record.timing is Timing.RESOLVE_AFTER_END
            and record.cards
            and record.cards[0].type == "dodge"
            for record in e.records
        ),
    )

    dodges = [card for card in _jie_used_products(engine) if card.type == "dodge"]
    assert len(dodges) == 1, "【闪】步里转出来的是一张【闪】"
    assert dodges[0].virtual_sources == (CLUB_CROSSBOW,)
    assert state.player(0).hp == 2, "【闪】成立 ⇒ 那张【杀】被抵消（座位 0 不掉血）"


def test_jie_jianying_converts_a_card_into_a_slash_in_the_luanwu_window():
    """**用户点名的窗口**：贾诩【乱武】的「对最近的角色使用【杀】」二选一。"""
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JIAXU, hp=3, hp_max=3),
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(CLUB_CROSSBOW,)),
        ),
    )
    engine = _jie_run(
        state,
        [
            _WindowSeat(activate=LUANWU),
            _WindowSeat(provider=True, source=CLUB_CROSSBOW),
        ],
        lambda e: _jie_closed(e, FrameKind.SKILL),
    )

    products = [card for card in _jie_used_products(engine) if card.type == "slash"]
    assert len(products) == 1, "乱武窗口里逼出的是一张【杀】（不是失体力那条）"
    assert products[0].virtual_sources == (CLUB_CROSSBOW,)
    assert state.player(0).hp == 2, "那张【杀】打向了最近的贾诩"
    assert state.player(1).hp == 2, "选项 2（失体力）没有发生"


def test_jie_jianying_is_not_offered_in_the_lord_request_window():
    """求助窗口（护驾 / 激将）**问不到**界沮授：``_request_askers`` 只问**同势力**角色。

    旧断言为了把 ② 塞进求助窗口，把座位 1（界沮授）强行改成 ``Kingdom.WEI``——但武将数据
    写死 ``jie_jushou_mobile`` 是**群**（`heroes.jie_yijiang_2014_mobile.json`），那张局面在
    真实对局里不可能出现，于是它只证明了"绕过势力判据时提供者会出现"，什么也没钉住。

    真实可及的读法：应答者 = ``seat != owner ∧ same_kingdom``（`lord_request.py` 的
    ``_request_askers``），而将池里带求助技能的只有**魏**【护驾】（曹操 / 界曹操）与
    **蜀**【激将】（刘备 / 界刘备）⇒ **没有群势力的主公**，群势力的界沮授永远进不了求助
    区间，② 在求助窗口里也就永远不会被提供。这条断言就是那个事实：求助窗口照常开（问到了
    同势力的队友），但从未问到座位 1，候选里也从未出现 ②。

    座位 1 此刻手里有可转化的源牌、又正处在**自己的出牌阶段**（Q17-b 的额度还没动）
    ⇒ 只要被问，② 一定"付得起"。所以"没被问"是它不出现的**唯一**原因，这条负断言不空。

    **遗留风险**（票 `20-lord-request-consumes-provider.md` 记录，未实现）：一旦有人能让
    魏 / 蜀的转化技能声明 ``dodge`` / ``respond`` 语境，它就会在求助窗口里**被提供**，然后
    被 `lord_request.py` 的 ``_run_lord_request`` 静默吞成一次放弃（那里只认 ``PlayCard``）
    —— 一个按下去没反应的死按钮。本票不改 `lord_request.py`。
    """
    slash = cards_of_type(PACK, "slash")[0]
    state = _jie_window_state(
        seats=(
            # 身份局的主公恒坐座位 0（`RuleConfig.first_seat` 不得预设），故由座位 1 的渐营
            # 持有者在自己的出牌阶段对曹操出【杀】——【护驾】的求助窗口就长在这里。
            SeatSpec(
                hero="caocao",
                hp=4,
                hp_max=4,
                identity=Identity.LORD,
                kingdom=Kingdom.WEI,
            ),
            # 界沮授按**武将数据**是群：这张局面在真实对局里可能出现。
            SeatSpec(
                hero=JUSHUO,
                hp=2,
                hp_max=3,
                hand=(slash, CLUB_CROSSBOW),
                kingdom=Kingdom.QUN,
            ),
            # 两名真实的魏势力队友：求助窗口问的是他们（而不是座位 1）。
            SeatSpec(
                hero="xiahoudun",
                hp=4,
                hp_max=4,
                identity=Identity.REBEL,
                kingdom=Kingdom.WEI,
            ),
            SeatSpec(
                hero="guojia",
                hp=3,
                hp_max=3,
                identity=Identity.REBEL,
                kingdom=Kingdom.WEI,
            ),
        ),
    )
    engine = _jie_run(
        state,
        [
            _WindowSeat(lord=HUJIA),
            _WindowSeat(use=slash, use_at=0),
            _WindowSeat(),
            _WindowSeat(),
        ],
        lambda e: _jie_closed(e, FrameKind.USE),
    )
    # 曹操先在自己的【闪】窗口里选了【护驾】；求助窗口于是逐个问同势力角色。
    assert any(
        isinstance(action, ActivateSkill) and action.skill_id == HUJIA
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.DODGE
        for action in record.query.legal_actions
    ), "这条用例的前提：曹操真的被问了【护驾】"

    lord_queries = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.RESPOND
        and getattr(record.query, "cause", None) is WindowCause.LORD_REQUEST
    ]
    assert lord_queries, "这条用例的前提：求助窗口真的开了（问到了同势力的队友）"
    assert {query.actor for query in lord_queries} == {2, 3}, (
        "求助窗口只问**同势力**角色（`_request_askers` 的 `same_kingdom`）：曹操是魏 ⇒ "
        "问座位 2 / 3，**不问**群势力的座位 1（界沮授）——"
        f"实际问到的是 {sorted(query.actor for query in lord_queries)}"
    )
    assert not any(
        isinstance(action, ActivateSkill) and action.skill_id == JIE_JIANYING
        for query in lord_queries
        for action in query.legal_actions
    ), "② 在求助窗口里不会被提供（提供者只在持有者自己被问的那一问里出现）"
    assert state.player(0).hp == 3, "求助没人接得住（界沮授问不到、两名队友也没【闪】）"


def test_jie_jianying_is_not_offered_in_the_judge_substitution_window():
    """改判窗口（登记表里"登记但排除"）：即便座位持有 ②，改判询问里也不出现提供者候选。"""
    from sgs.skills.catalog import CARD_REQUEST_WINDOWS

    row = next(row for row in CARD_REQUEST_WINDOWS if row.key == "rejudge")
    assert row.providers is False

    indulgence = cards_of_type(PACK, "indulgence")[0]
    original = _basic_with_suit(Suit.HEART)
    black = CLUB_CROSSBOW
    assert _deck_card(black).suit is Suit.CLUB
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(),
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(black,), judge=(indulgence,)),
        ],
        deck=(original, *deck_excluding(PACK, indulgence, original, black)),
        turn_seat=1,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=1),
    )
    # 授予【鬼道】⇒ 座位 1（渐营持有者）真的被拉进改判窗口。
    state.player(1).granted_skills.append(GUIDAO)
    engine = _jie_run(
        state,
        [_WindowSeat(), _WindowSeat()],
        lambda e: (
            e.pending is not None
            and e.pending.kind == QueryKind.RESPOND
            and getattr(e.pending, "cause", None) is WindowCause.REJUDGE
        ),
    )

    window = engine.pending
    assert window.actor == 1 and window.cause is WindowCause.REJUDGE
    assert [
        action for action in window.legal_actions if isinstance(action, SubstituteJudgeCard)
    ], "改判窗口真的开着（候选是 SubstituteJudgeCard）"
    assert not [
        action
        for action in window.legal_actions
        if isinstance(action, ActivateSkill)
    ], "改判既不是使用也不是打出 ⇒ 提供者一律不参与"


def test_jie_jianying_provider_lists_only_the_required_name_in_its_manifest():
    """窗口里牌名被窗口定死 ⇒ ② 只列**要的那一个**牌名，交互只剩"选哪张源牌"一步。

    出牌阶段那份清单列全三个牌名（Q-J5 = A）；窗口里 ``required`` 说了要什么，故第二次
    询问（牌名）不出现——同一份声明按语境少一步，产物就是那个牌名。
    """
    from sgs.effects import CandidateScope
    from sgs.skills import active as active_module

    declare = getattr(active_module, JIE_JIANYING_WINDOWS)
    assert declare.scopes == frozenset(
        {CandidateScope.DODGE, CandidateScope.PEACH, CandidateScope.RESPOND}
    ), "声明只覆盖三档要牌语境（无懈不在：② 的产物是基本牌）"

    barbarian = cards_of_type(PACK, "barbarian_invasion")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(CLUB_CROSSBOW,)),
            SeatSpec(hand=(barbarian,)),
        ),
        turn_seat=1,
    )
    seat = _WindowSeat(provider=True, source=CLUB_CROSSBOW)
    engine = _jie_run(
        state,
        [seat, _WindowSeat(use=barbarian)],
        lambda e: ran_timing(e, Timing.PLAY_ON_PLAY),
    )

    kinds = [
        str(getattr(query.kind, "value", query.kind))
        for query in seat.asked
        if str(getattr(query.kind, "value", query.kind)) in ("SELECT_CARDS", "SELECT_OPTION")
    ]
    assert kinds == ["SELECT_CARDS"], (
        f"窗口里只问『选哪张源牌』（牌名已被窗口定死）：{kinds}"
    )
    product = _jie_played_cards(engine)[0]
    assert product.type == "slash", "产物就是窗口要的那一个牌名（不是闪 / 桃）"
    assert product.name == "杀"


def test_jie_jianying_does_not_spend_the_play_phase_quota_outside_its_own_play_phase():
    """Q17-b = A：回合外的窗口发动**不查也不记** ``_USED`` ⇒ 回到自己的出牌阶段仍可发动。"""
    barbarian = cards_of_type(PACK, "barbarian_invasion")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(CLUB_CROSSBOW,)),
            SeatSpec(hand=(barbarian,)),
        ),
        turn_seat=1,
    )
    engine = _jie_run(
        state,
        [_WindowSeat(provider=True, source=CLUB_CROSSBOW), _WindowSeat(use=barbarian)],
        lambda e: (
            e.pending is not None
            and e.pending.kind == QueryKind.PLAY_PHASE
            and e.pending.actor == 0
        ),
    )

    assert _jie_played_cards(engine), "前提：那张【杀】真的在别人的回合里打出去了"
    assert state.turn_seat == 0 and state.phase is Phase.PLAY
    assert _jie_manifest(engine.pending), (
        "回合外的那次发动不占额度：自己的出牌阶段里 ② 的清单**照旧出现**"
    )


def test_jie_jianying_still_spends_the_quota_inside_its_own_play_phase():
    """Q17-b 的另一半（**边角**）：自己的出牌阶段里因响应窗口而发动 ⇒ 按字面**算**额度。

    边角用例逐字取自票面 §3：自己造成伤害触发的濒死求桃长在自己的出牌阶段内
    （``turn_seat == owner`` ∧ 阶段 = 出牌阶段）⇒ 这一次照样花掉「出牌阶段限一次」。
    """
    slash = cards_of_type(PACK, "slash")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(slash, CLUB_CROSSBOW)),
            SeatSpec(hp=1, hp_max=4),
        ),
    )
    engine = _jie_run(
        state,
        [_WindowSeat(use=slash, provider=True, source=CLUB_CROSSBOW), _WindowSeat()],
        lambda e: (
            _jie_closed(e, FrameKind.NEAR_DEATH)
            and e.pending is not None
            and e.pending.kind == QueryKind.PLAY_PHASE
            and e.pending.actor == 0
        ),
    )

    assert sorted(card.type for card in _jie_used_products(engine)) == [
        "peach",
        "slash",
    ], "前提：求桃窗口里的那一次 ② 真的发动过（那张虚拟【桃】走了使用链）"
    assert state.phase is Phase.PLAY and state.turn_seat == 0
    assert _jie_manifest(engine.pending) == (), (
        "自己的出牌阶段里的响应窗口**也算**额度：本阶段不再有入口"
    )
    assert not [
        action
        for action in engine.pending.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == JIE_JIANYING
    ]


def test_jie_jianying_keeps_the_source_face_in_a_window_outside_its_play_phase():
    """Q17-c = A：回合外的窗口里"本阶段没有上一张使用牌" ⇒ 继承来源牌，**不读陈旧值**。"""
    barbarian = cards_of_type(PACK, "barbarian_invasion")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(HEART_DODGE_TWO,)),
            SeatSpec(hand=(barbarian,)),
        ),
        turn_seat=1,
    )
    # 上一次（自己回合的）出牌阶段留下的陈旧值：座位事实还指着那张 ♣A。
    state.player(0).play_phase_last_card = _deck_card(CLUB_CROSSBOW)
    engine = _jie_run(
        state,
        [_WindowSeat(provider=True, source=HEART_DODGE_TWO), _WindowSeat(use=barbarian)],
        lambda e: ran_timing(e, Timing.PLAY_ON_PLAY),
    )

    product = _jie_played_cards(engine)[0]
    assert product.type == "slash"
    assert product.suit == "heart", "继承来源牌的花色（♥），不是上一回合留下的 ♣"
    assert product.rank == _deck_card(HEART_DODGE_TWO).rank, "点数同样来自源牌"
    assert product.color.value == "red", "颜色由花色派生（红）"


def test_jie_jianying_keeps_the_previous_suit_inside_its_own_play_phase():
    """Q17-c 的另一半：自己的出牌阶段 ⇒ 花色取"**本阶段**使用的上一张牌"（Q-J4 口径不变）。"""
    duel = next(
        card
        for card in cards_of_type(PACK, "duel")
        if _deck_card(card).suit is not Suit.HEART
    )
    slash = cards_of_type(PACK, "slash")[0]
    state = _jie_window_state(
        seats=(
            SeatSpec(hero=JUSHUO, hp=3, hp_max=3, hand=(duel, HEART_DODGE_TWO)),
            SeatSpec(hand=(slash,)),
        ),
    )
    engine = _jie_run(
        state,
        [
            _WindowSeat(use=duel, use_at=1, provider=True, source=HEART_DODGE_TWO),
            _WindowSeat(real=slash),
        ],
        lambda e: any(
            record.kind == "timing"
            and record.timing is Timing.PLAY_ON_PLAY
            and record.cards
            and record.cards[0].virtual_sources
            for record in e.records
        ),
    )

    product = next(card for card in _jie_played_cards(engine) if card.virtual_sources)
    assert product.type == "slash"
    assert product.suit == _deck_card(duel).suit, (
        "花色 = 本阶段使用的上一张牌（那张【决斗】），不是来源牌的 ♥"
    )
    assert product.rank == _deck_card(HEART_DODGE_TWO).rank, "点数仍继承来源牌"


# ===========================================================================
# yijiang-2014-round2 票 18（P9）：② 的牌名合法性 = **此刻能不能用**；
# 目标选择进预发动清单第 3 步
#
# 用户 2026-11-09 逐字：「转化预发动选择要使用什么牌名时，没有考虑所有牌名的牌的合法性
# （例如我没有杀的次数了，但是还能转化杀）预期：修改为预发动时检验合法性，并且选择某牌名
# 后，选择目标也纳入预发动第三步，必须散步都选择完毕才能提交」（"散步" = 三步）。
#
# 两处判据都**不新造规则**：
#
# - 牌名置灰 = 「这张产物此刻能不能用」（次数 / 时机 / 目标池一起判）。旧实现只查
#   ``check_targets``，于是次数用尽的【杀】仍在闭集里、不置灰；
# - 第 3 步 = 产物的 ``TargetRule``：**有得选才出现**（0 个目标 / 池恰好装得下额定数 ⇒
#   这一步由牌名决定，直接跳过）。判据与提交后的 ``_jie_jianying_targets`` 同一份。
# ===========================================================================

PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""160 档牌堆：只有它才有【酒】（108 档的牌名闭集是杀 / 闪 / 桃）。"""


def _jie_ready_four(*, hand=(), hp: int = 3, hp_max: int = 3):
    """4 人身份局（``RuleConfig`` 没有 3 人档）：座位 0 = 界沮授（主公），其余反贼。

    只有"目标不止一个"的场面才需要它：【杀】的目标步要有**得选**，池里至少两名——4 人环上
    座位 0 的攻击范围 1 恰好够到左右两家（座位 2 隔了一位，不在池里）。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hero=JUSHUO, hp=hp, hp_max=hp_max, hand=tuple(hand), identity=Identity.LORD
            ),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.REBEL),
        ],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4, first_seat=0),
    )
    return state, _jie_drive(Engine.from_state(state, seed=7), _jie_is_idle)


def _jie_assert_disabled(steps, name: str) -> None:
    """牌名步：``name`` **在闭集里**（列全）但被置灰（此刻用不了）。"""
    name_step = next(step for step in steps if step.name == "name")
    assert name in _jie_step_options(name_step), f"{name} 不在闭集里（Q-J5 = A 要求列全）"
    assert name in _jie_step_disabled(name_step), f"{name} 此刻用不了，却没置灰"


def _jie_assert_enabled(steps, name: str) -> None:
    name_step = next(step for step in steps if step.name == "name")
    assert name in _jie_step_options(name_step)
    assert name not in _jie_step_disabled(name_step), f"{name} 此刻能用，却被置灰了"


def test_jie_jianying_greys_the_slash_name_when_the_slash_quota_is_spent():
    """**本票的判据点**：本回合【杀】的次数用尽 ⇒ 不能把牌转成【杀】（置灰 + 校验拒收）。

    旧判据只看目标池（`check_targets`），而"目标池非空"与"这张牌此刻还能用"是两件事：
    次数额度是**使用校验**的另一半。这里真的打出一张【杀】把额度花掉（不是往状态里塞
    一个计数），再看清单。
    """
    from sgs.skills import validate_activation

    slash = cards_of_type(PACK, "slash")[0]
    state, engine = _jie_ready_four(hand=(slash,), hp=2, hp_max=3)
    assert _jie_manifest(engine.pending), "前提：本阶段还没用过 ②"
    _jie_assert_enabled(_jie_manifest(engine.pending), "杀")

    _jie_use_and_settle(engine, slash, targets=(1,))

    steps = _jie_manifest(engine.pending)
    assert steps, "【桃】此刻仍可用（2/3 体力）⇒ ② 的清单还在（不是整体消失）"
    _jie_assert_disabled(steps, "杀")
    assert [step.name for step in steps] == ["card", "name"], (
        "【桃】的目标由牌名决定（直取自己）⇒ 不出现空的第 3 步"
    )
    assert validate_activation(state, 0, JIE_JIANYING, (slash,), ("杀",)) is not None, (
        "置灰不是界面纪律：次数用尽时复合动作仍被服务端拒收"
    )


def test_jie_jianying_keeps_the_slash_name_available_while_the_quota_lasts():
    """反向断言：额度还在 ⇒ 【杀】不置灰（同一个局面、只差"打没打出那张【杀】"）。"""
    slash = cards_of_type(PACK, "slash")[0]
    state, engine = _jie_ready_four(hand=(slash,), hp=2, hp_max=3)

    assert state.player(0).card_use_counts.get("slash", 0) == 0
    _jie_assert_enabled(_jie_manifest(engine.pending), "杀")


def test_jie_jianying_greys_the_wine_name_after_wine_was_used_this_turn():
    """【酒】的"每回合一次"也是使用校验的一半：喝过之后牌名置灰（160 档才有【酒】）。"""
    wine = cards_of_type(PACK_160, "wine")[0]
    source = cards_of_type(PACK_160, "qilin_bow")[0]
    state = build_state(
        pack=PACK_160,
        seats=[
            SeatSpec(hero=JUSHUO, hp=3, hp_max=3, hand=(wine, source)),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK_160, wine, source),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = _jie_drive(Engine.from_state(state, seed=7), _jie_is_idle)
    _jie_assert_enabled(_jie_manifest(engine.pending), "酒")

    _jie_use_and_settle(engine, wine)

    steps = _jie_manifest(engine.pending)
    assert steps, "【杀】仍可用 ⇒ 清单还在"
    _jie_assert_disabled(steps, "酒")


def test_jie_jianying_lists_targets_as_the_third_manifest_step():
    """清单三步：选牌 → 牌名 → 目标；第 3 步的池 = 产物的合法目标（按牌名分行）。"""
    state, engine = _jie_ready_four(hand=(HEART_BOW,), hp=3, hp_max=3)
    steps = _jie_manifest(engine.pending)

    assert [step.name for step in steps] == ["card", "name", "seat"], (
        "有得选的目标 ⇒ 目标成为预发动的第 3 步"
    )
    seat_step = steps[2]
    assert (seat_step.min, seat_step.max) == (1, 1), "【杀】额定 1 个目标"
    rows = seat_step.pool
    assert rows, "第 3 步要下发候选（否则界面只能靠退化档）"
    assert all(
        "杀" not in row.when or row.when[-1] == "杀" for row in rows
    ), f"逐行的前序答案里只有『杀』那一族能要目标：{[row.when for row in rows]}"
    options = {
        option
        for row in rows
        for option in row.options
        if not row.when or row.when[-1] == "杀"
    }
    assert options == {"seat:1", "seat:3"}, (
        f"4 人环上攻击范围 1 ⇒ 【杀】够得着左右两家（隔位那家不行）：{sorted(options)}"
    )
    assert all(row.max == 1 for row in rows if not row.when or row.when[-1] == "杀")


def test_jie_jianying_skips_the_target_step_for_a_targetless_name():
    """目标由牌名决定的牌名（【桃】/【酒】）**不出现**空的第 3 步。

    两条证据：

    - 只剩【桃】/【酒】可用时清单只有两步（没有第 3 步可填）；
    - 清单里有【杀】那一行时，同一个目标步里【桃】那一行被标成"没有可选目标"
      （``max == 0``、池为空）——界面据此自动跳过，不画空步。
    """
    # (1) 108 档、2 人局、【桃】：对手【空城】⇒ 【杀】没有合法目标；2/3 体力 ⇒ 只有【桃】。
    state = _jie_state(hand=(HEART_BOW,), hp=2, hp_max=3, seats=2)
    state.player(1).granted_skills.append("hero.zhugeliang.kongcheng")
    engine = _jie_drive(Engine.from_state(state, seed=7), _jie_is_idle)
    steps = _jie_manifest(engine.pending)
    assert set(_jie_step_options(steps[1])) - set(_jie_step_disabled(steps[1])) == {"桃"}
    assert [step.name for step in steps] == ["card", "name"], (
        "【桃】的目标由牌名决定（自己）⇒ 不出现第 3 步"
    )

    # (2) 160 档、【酒】：同样是"自己"这个唯一目标。
    wine = cards_of_type(PACK_160, "wine")[0]
    source = cards_of_type(PACK_160, "qilin_bow")[0]
    state_160 = build_state(
        pack=PACK_160,
        seats=[SeatSpec(hero=JUSHUO, hp=3, hp_max=3, hand=(wine, source)), SeatSpec()],
        deck=deck_excluding(PACK_160, wine, source),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    state_160.player(1).granted_skills.append("hero.zhugeliang.kongcheng")
    engine_160 = _jie_drive(Engine.from_state(state_160, seed=7), _jie_is_idle)
    steps_160 = _jie_manifest(engine_160.pending)
    usable = set(_jie_step_options(steps_160[1])) - set(_jie_step_disabled(steps_160[1]))
    assert "酒" in usable, f"前提：【酒】此刻可用（实得 {sorted(usable)}）"
    assert usable == {"酒"}, f"【杀】一族被【空城】挡住：只有【酒】可用（实得 {sorted(usable)}）"
    assert [step.name for step in steps_160] == ["card", "name"], (
        "【酒】的目标也是唯一的 ⇒ 不出现第 3 步"
    )

    # (3) 第 3 步存在时（【杀】有得选），【桃】那一行是"没有可选目标"。
    state_mixed, engine_mixed = _jie_ready_four(hand=(HEART_BOW,), hp=2, hp_max=3)
    steps_mixed = _jie_manifest(engine_mixed.pending)
    assert [step.name for step in steps_mixed] == ["card", "name", "seat"]
    peach_rows = [row for row in steps_mixed[2].pool if row.when[-1:] == ("桃",)]
    assert peach_rows, "同一个目标步里【桃】也有一行（按牌名分行）"
    assert all(row.options == () and row.max == 0 for row in peach_rows), (
        f"【桃】那一行 = 没有可选目标（界面自动跳过）："
        f"{[(row.options, row.max) for row in peach_rows]}"
    )


def test_jie_jianying_manifest_is_unsubmittable_until_the_target_step_is_filled():
    """第 3 步没填 ⇒ 复合动作被拒；填了合法目标 ⇒ 放行，且产物真的打向它。"""
    from sgs.skills import validate_activation

    state, engine = _jie_ready_four(hand=(HEART_BOW,), hp=2, hp_max=3)

    assert validate_activation(state, 0, JIE_JIANYING, (HEART_BOW,), ("杀",)) is not None, (
        "三步没填完（缺目标）⇒ 服务端拒收"
    )
    assert validate_activation(
        state, 0, JIE_JIANYING, (HEART_BOW,), ("杀", "seat:1")
    ) is None, "填了一个合法目标 ⇒ 放行"
    assert validate_activation(
        state, 0, JIE_JIANYING, (HEART_BOW,), ("杀", "seat:9")
    ) is not None, "非法目标（没有这个座位）照样拒收"
    assert validate_activation(
        state, 0, JIE_JIANYING, (HEART_BOW,), ("杀", "seat:2")
    ) is not None, "隔位的角色在攻击范围外 ⇒ 也不在池里"
    assert validate_activation(state, 0, JIE_JIANYING, (HEART_BOW,), ("桃",)) is None, (
        "【桃】的目标由牌名决定 ⇒ 不要求第 3 步的答案"
    )

    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=JIE_JIANYING,
            picks=(HEART_BOW,),
            choices=("杀", "seat:1"),
            answers_seq=query.seq,
        )
    )
    engine = _jie_drive(engine, _jie_is_idle)

    assert state.player(1).hp == 3, "产物打向第 3 步选中的那一名"
    assert [state.player(seat).hp for seat in (2, 3)] == [4, 4], "没选的那两名不受影响"


# ===========================================================================
# 界沮授移动版【矢北】（Q-J1 = A：OL 口径 ⇒ 开局 3 点护甲 + 受伤后两支）
# ===========================================================================


def test_jie_shibei_is_locked_and_reuses_the_ol_callbacks():
    """锁定技（不问）+ 两条钩子；受伤那一支的**回调与归属判据**都与 OL 版共用同一对象。"""
    from sgs.skills import CATALOG, flow_for
    from sgs.skills.damage_after import _shibei, _shibei_condition

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
    # 票 12（P3）：受伤那一支的自校（受伤者是不是持有者）也**是同一个纯函数**——
    # 与 OL 版同源，也与【耀武】/【刚烈】等同族钩子用的那一条（`_hurt_owner`）同源。
    assert definition.hooks[1].condition is _shibei_condition
    assert definition.hooks[1].condition is ol.hooks[0].condition, "界版复用 OL 的归属判据"
    assert definition.hooks[0].condition is None, "开局发护甲那一条不关于「谁受伤」，没有条件"


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


def test_jie_shibei_does_not_fire_when_someone_else_is_hurt():
    """界【矢北】同样只认「自己受伤」（yijiang-2014-round2 票 12 / P3 §5）。

    用户 2026-11-09 实机报障的界版逐字：「界沮授和标沮授的【矢北】都存在一个问题："当你受到
    伤害后"没有筛选自身，其他人受到伤害也能触发矢北，然后自身会回血」。归属判据与 OL 版
    共用同一个纯函数（``_hurt_owner``，见上面的结构面对照）；这里从外部缝看行为：别人挨打，
    界沮授不回血、不进记录流。

    持有者取 2/3 体力：满体力那条分支（满血不压帧）会把"该红"的用例抹成绿，故不能取 3。
    """
    state, engine = _jie_hurt_state(hp=2)
    _jie_hit(engine, source=0, target=1)

    assert state.player(0).hp == 2, "受伤的是座位 1，界沮授一点体力都不回"
    assert state.player(1).hp == 3, "只掉座位 1 自己的体力"
    assert state.player(0).damage_taken_this_turn == 0, "受伤者不是持有者 ⇒ 不占名额"
    assert not [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.RECOVER_VALUE
    ], "不压 RECOVER 帧（别人的改回复量技能也看不到这一次）"


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


def test_jie_jianying_accepts_any_own_card_as_source_regardless_of_its_own_legality():
    """② 的第 1 步（源牌）**不看这张牌自己能不能用**——票 19 证伪的那一半，这里钉成不变量。

    票面怀疑"【渐营】的合法性检验搞到源牌上了"。实测不是：源牌步 = ``PickCards(zones=
    HAND/EQUIP, key="card")``，候选是**自己的每一张手牌 / 装备牌**；牌名合法性只落在**产物**
    （``_jie_jianying_name_disabled`` / ``_jie_jianying_entry``），``check_targets`` 也只对
    产物调用。用一张黑色【借刀杀人】钉住：2 人局双方都没武器 ⇒ 它自己
    ``check_targets == ()``（一个合法目标都没有），但源牌步里照样可选、不置灰。
    """
    from sgs.effects import check_targets

    borrowed = next(
        card_id
        for card_id, card in enumerate(PACK.deck.cards)
        if card.type == "borrowed_sword" and color_of_suit(card.suit) is Color.BLACK
    )
    state, engine = _jie_ready(hand=(borrowed,), hp=2, hp_max=3)

    assert check_targets(state, 0, state.card(borrowed)) == (), (
        "前提：这张牌自己此刻一个合法目标都没有（双方都没武器）"
    )
    steps = _jie_manifest(engine.pending)
    assert steps and steps[0].name == "card", (
        f"清单第一步 = 选源牌，实得 {[step.name for step in steps]}"
    )
    options = {int(option) for option in _jie_step_options(steps[0])}
    assert options == set(state.player(0).hand), (
        f"源牌候选 = 自己的**每一张**手牌（与它自己能不能用无关）：{sorted(options)}"
    )
    assert str(borrowed) in _jie_step_options(steps[0])
    assert _jie_step_disabled(steps[0]) == (), "源牌步没有置灰（它不是产物，不查牌名合法性）"


# ===========================================================================
# 界陈群移动版【法恩】（yijiang-2014-round2 票 11 / P2）
#
# 牌面（用户 `技能.txt` 逐字，与 OL 陈群【法恩】**逐字相同**）：
#
#     当一名角色翻面或横置后，你可以令其摸一张牌。
#
# 落法（票面 §2，沿用票 08 / 票 10 的结论，不重新设计）：**同一组回调**（`_chenqun_faen`
# / `_faen_seat`）、**同一个方向判据**（`_faen_chain_is_set`）与**同一份零步流水线**
# （`CHENQUN_FAEN_FLOW`，不另起 flow 名）+ 第二条 `CATALOG` 条目。方向判据不重写 ⇒
# 界版**天然**继承票 10 的口径：横置问、重置不问、翻面两个方向都问。
#
# 判据全走**外部缝**：两个写入口的返回值、记录流里的询问 / delta、座位上的状态位与手牌。
# 不断言内部结构，也不 mock。
# ===========================================================================

JIE_FAEN = "hero.jie_chenqun_mobile.faen"
OL_FAEN = "hero.chenqun.faen"
FAEN_FACE = "当一名角色翻面或横置后，你可以令其摸一张牌"


def _faen_state(*, seats: int = 2, hand: tuple[int, ...] = ()):
    """座位 0 = 界陈群（3 体力，``hand`` 直接给定）；牌堆 = 除 ``hand`` 外的前 10 张。

    缺省从**出牌阶段**起跑：不经过摸牌阶段 ⇒ 技能持有者的手牌就是 ``hand``（"谁摸到牌"
    才好读）。
    """
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hp=3, hp_max=3, hand=hand)]
        + [SeatSpec() for _ in range(seats - 1)],
        deck=deck_excluding(PACK, *hand)[:10],
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=seats),
    )


def _faen_engine(*, seed: int = 1, **kwargs):
    return Engine.from_state(_faen_state(**kwargs), seed=seed)


def _faen_asks(engine) -> list:
    """记录流里的界【法恩】「是否发动」询问（按出现顺序）。"""
    return [
        record
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and str(record.query.skill_id) == JIE_FAEN
    ]


class _FaenScript:
    """脚本玩家：对界【法恩】的每一问按给定序列答「发动 / 放弃」，其余走最小应答。

    ``self.asked`` 记下每一次被问的界【法恩】询问——"问了几次"从这条外部缝读。
    """

    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.asked: list = []
        self._otherwise = RecordingPlayer()

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION and str(query.skill_id) == JIE_FAEN:
            self.asked.append(query)
            take = self.answers.pop(0) if self.answers else False
            if take:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        return self._otherwise.respond(query)


def _faen_players(player: "_FaenScript", *, seats: int = 2) -> list:
    """座位 0 = 会答界【法恩】的那名脚本玩家；其余座位只做最小应答。"""
    return [player, *(_FaenScript() for _ in range(seats - 1))]


def _settle_after_the_dispatch(engine, players) -> None:
    """推进到「这一趟状态位派发**收尾之后**」：本局的第一个「回合开始前」是那个里程碑。

    ``Engine.submit`` 只**登记**应答，引擎在下一条 ``_step`` 才消费它 ⇒ 停止点取"应答之后
    才出现的公开事实"。本局从 ``phase=PLAY`` 的造局起跑、回合环从 ``GAME_START`` 重跑，
    故「回合开始前」排在派发帧之后、摸牌阶段之前。
    """
    run_queries(engine, players, lambda e: ran_timing(e, Timing.TURN_BEFORE_START))


def _turn_before_starts(engine) -> int:
    """记录流里「回合开始前」的次数（停止点用：重置那一次之后引擎还会再进一次）。"""
    return len(
        [
            record
            for record in engine.records
            if record.kind == "timing" and record.timing is Timing.TURN_BEFORE_START
        ]
    )


def test_jie_chenqun_faen_draws_for_the_seat_that_flipped_or_chained():
    """两个时机各问一次 ⇒ **刚翻面 / 横置的那名角色**摸一张（「令其」≠ 技能持有者）。"""
    flipped = _faen_engine()
    flip_player = _FaenScript(True)
    assert flipped.set_flipped(None, 1, True) is True
    _settle_after_the_dispatch(flipped, _faen_players(flip_player))

    assert len(flip_player.asked) == 1, "翻面（值真的变了）⇒ 问一次"
    assert flipped.state.player(1).flipped is True
    assert len(flipped.state.player(1).hand) == 1, "「令其摸一张牌」= 刚翻面的那名角色"
    assert flipped.state.player(0).hand == [], "技能持有者自己不摸"

    chained = _faen_engine()
    chain_player = _FaenScript(True)
    assert chained.set_chained(None, 1, True) is True
    _settle_after_the_dispatch(chained, _faen_players(chain_player))

    assert len(chain_player.asked) == 1, "横置是第二个独立时机（两条独立钩子）"
    assert chained.state.player(1).chained is True
    assert len(chained.state.player(1).hand) == 1
    assert chained.state.player(0).hand == []


def test_jie_chenqun_faen_does_not_fire_on_a_chain_reset():
    """**与票 10 同一条口径**（界版回归）：已横置的座位 1 被属性【杀】命中 ⇒ 重置**不问**。

    造局走用户报障的那条真实路径（属性伤害 ⇒ 写入口把 ``chained`` 写回 ``False``），
    不旁路写入口、也不 mock。断言分三层（与 OL 版那条逐层同形）：① 重置本身照旧派发
    （框架口径不变）；② 记录流里没有界【法恩】的「是否发动」；③ 那一问若发生过就一定会
    摸牌（脚本对每问都答"发动"），故"手牌为空"同时证明"没问"与"没摸"。
    """
    pack = load_content_pack(deck_profile="standard_ex_160")
    fire_slash = cards_of_type(pack, "fire_slash")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero=HERO, hp=3, hp_max=3, hand=(fire_slash,)),
            SeatSpec(chained=True),
        ],
        deck=deck_excluding(pack, fire_slash),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=1)
    player = _FaenScript(True)
    players = _faen_players(player)

    _drive(engine, _at_play_phase)
    slash = next(
        action
        for action in engine.pending.legal_actions
        if isinstance(action, UseCard)
        and action.card.card_id == fire_slash
        and action.targets == (1,)
    )
    engine.submit(slash)
    run_queries(engine, players, lambda e: _turn_before_starts(e) >= 2)

    assert engine.state.player(1).chained is False, "属性伤害命中横置者 ⇒ 结算后立即重置"
    assert [
        record.chained for record in _jie_deltas(engine, "chained_change")
    ] == [False], "派发口径不变（读法一）：重置照旧落一条 chained_change"
    assert ran_timing(engine, Timing.STATE_CHAINED), (
        "重置照旧是一次 STATE_CHAINED 事件（票 10 只动技能侧，不动派发）"
    )
    assert player.asked == [], "重置不是「横置后」⇒ 连「是否发动」都不问（票 10 的判据被复用）"
    assert _faen_asks(engine) == [], "记录流里也没有这一问"
    assert engine.state.player(1).hand == [], "没问 ⇒ 一张都不摸"


def test_jie_chenqun_faen_is_declared_and_wired():
    """**数据行 + ``CATALOG`` + ``descriptions.json`` 三处一致**，且与 OL 那条共用实现。

    加载期 ``loader`` 对启用武将逐条查 ``CATALOG``（数据行声明什么就必须有什么），
    反方向由 ``test_hero_catalog`` 的 ``actual == expected`` 钉住 ⇒ 三处必须同一次提交。
    """
    import json
    from pathlib import Path

    from sgs.data import load_heroes
    from sgs.skills import CATALOG, flow_for
    from sgs.skills.state_after import _faen_chain_is_set

    table = load_heroes("jie_yijiang_2014_mobile")
    hero = next(hero for hero in table.heroes if hero.id == HERO)
    assert [(skill.id, skill.name, skill.skill_type.value) for skill in hero.skills] == [
        ("dingpin", "定品", "NORMAL"),
        ("faen", "法恩", "NORMAL"),
    ], "票面 §2：数据行追加第二条技能（与 CATALOG 同一次落地）"

    definition = CATALOG[JIE_FAEN]
    assert definition.carrier.value == "HERO"
    assert definition.skill_type.value == "NORMAL"
    assert definition.locked is False, "牌面写「可以」⇒ 非锁定（派发环先问「是否发动」）"
    assert definition.requirement == FAEN_FACE
    assert [hook.timing for hook in definition.hooks] == [
        Timing.STATE_FLIPPED,
        Timing.STATE_CHAINED,
    ], "翻面 / 横置是两个独立状态（§13.1 / §8.10）⇒ 两条独立钩子"

    ol = CATALOG[OL_FAEN]
    flipped_hook, chained_hook = definition.hooks
    assert flipped_hook.condition is None, (
        "翻面维持现状：两个方向都算一次事件（Q-W2 = 读法一）⇒ 不设条件"
    )
    assert chained_hook.condition is ol.hooks[1].condition, (
        "**复用**票 10 的方向判据（对象同一）：重置不问、横置问"
    )
    assert chained_hook.condition is _faen_chain_is_set
    assert flipped_hook.flow is ol.hooks[0].flow, "复用 OL 那一条的零步流水线对象"
    assert chained_hook.flow is flipped_hook.flow
    flow = flow_for(JIE_FAEN)
    assert flow is flow_for(OL_FAEN), "两版共用同一个 SkillFlow 对象（票面 §2）"
    assert flow.name == "chenqun_faen", "不另起 flow 名（票面 §2 明写复用对象）"
    assert flow.steps == (), "零步：只问「是否发动」（shapes.md §2 第 1 行）"

    path = Path(__file__).resolve().parent.parent / "tools" / "sgs_web" / "descriptions.json"
    descriptions = json.loads(path.read_text(encoding="utf-8"))["skills"]
    assert JIE_FAEN in descriptions, "本行有自己的 descriptions.json 条目（不借用 OL 那一行）"


def test_jie_chenqun_wording_is_its_own_row():
    """A4：界版与 OL 同文 ⇒ **两行都存在且牌面逐字相同**（不是借用 / 共享一条声明）。

    ``SkillDef`` 没有 ``shared_with`` 那种派生口子（票 08 的 Answer 已裁定不补）⇒ 两版各自
    一条 ``CATALOG`` 条目 + 各自一条 ``descriptions.json`` 条目；**牌面**（``requirement`` 与
    描述的首句）逐字相同，版本口径的说明落在描述后半段（与【矢北】两行同一姿态）。
    """
    import json
    from pathlib import Path

    from sgs.skills import CATALOG

    ours = CATALOG[JIE_FAEN]
    ol = CATALOG[OL_FAEN]
    assert ours.skill_id != ol.skill_id, "两条 CATALOG 条目（不是一份共享声明）"
    assert ours.requirement == FAEN_FACE == ol.requirement, "两版牌面逐字相同"

    path = Path(__file__).resolve().parent.parent / "tools" / "sgs_web" / "descriptions.json"
    descriptions = json.loads(path.read_text(encoding="utf-8"))["skills"]
    assert JIE_FAEN in descriptions and OL_FAEN in descriptions, "两行描述都存在"
    ours_text = descriptions[JIE_FAEN]
    ol_text = descriptions[OL_FAEN]
    assert ours_text.startswith(FAEN_FACE), "界版描述以牌面逐字开头"
    assert ol_text.startswith(FAEN_FACE), "OL 版描述以同一句牌面开头"
    assert ours_text.split("。", 1)[0] == ol_text.split("。", 1)[0] == FAEN_FACE, (
        "首句（牌面）逐字相同"
    )
    assert "铁索连环后" not in ours_text, "本仓按状态事件读（读法一），不是牌名事件"
    assert "重置" in ours_text, "界版描述带上票 10 的方向口径（重置不问，界版复用同一判据）"


# ===========================================================================
# 界顾雍移动版【秉壹】（yijiang-2014-round2 票 13 / P4）
#
# 牌面（界版逐字；与 OL 顾雍【秉壹】只差**条件**那一处）：
#
#     结束阶段，你可以展示所有手牌。若你的手牌颜色**或类别**均相同，
#     你令至多等同于你手牌数名角色各摸一张牌。
#
# 落法（票面 §3）：**第二条声明 + 同一个手写主体**（`phase_body._bingyi_effect`），
# 条件参数化（颜色 ∨ 类别，先例 = 界张角【黄天】按 `skill_id` 参数化 helper）；A4 ⇒
# 自己的 `requirement` / `step_requirements` 与自己的 `SkillFlow` 名（`jie_guyong_bingyi`）。
# 三条既有读法原样沿用：1 张手牌 = 平凡成立（`04-bingyi-uniform.md`）、0 张不给入口
# （`02-guyong.md` Q3 = A）、「至多 N 名」= 上限 + 可放弃（N = 展示那一刻的手牌数、含自己）。
#
# 判据全走**外部缝**：询问（`SKILL_ACTIVATION` / `SELECT_OPTIONS` 与其 `skill_id`）、
# 记录流里的 `card_reveal` / `card_move`、座位上的手牌。不断言内部结构，也不 mock。
# ===========================================================================

JIE_BINGYI = "hero.jie_guyong_mobile.bingyi"
OL_BINGYI = "hero.guyong.bingyi"
OL_GUYONG = "guyong"
OL_BINGYI_FACE = (
    "结束阶段，你可以展示所有手牌。若你的手牌颜色均相同，"
    "你令至多等同于你手牌数名角色各摸一张牌"
)
JIE_BINGYI_FACE = (
    "结束阶段，你可以展示所有手牌。若你的手牌颜色或类别均相同，"
    "你令至多等同于你手牌数名角色各摸一张牌"
)


def _bingyi_state(
    *, mine: tuple[int, ...], hero: str = GUYONG, seats: int = 2, turn_seat: int = 0
):
    """座位 0 = ``hero``（``mine`` = 直接给定的手牌）、其余座位空着。

    摸牌阶段的张数由 :func:`_park_bingyi` 置 0 ⇒ **结束阶段的手牌 = ``mine``**；体力 3 / 上限 3
    ⇒ 手牌 ≤ 3 时弃牌阶段一张都不弃（N 因此也只由 ``mine`` 决定）。
    """
    specs = [SeatSpec() for _ in range(seats)]
    specs[0] = SeatSpec(hero=hero, hp=3, hp_max=3, hand=mine)
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck_excluding(PACK, *mine),
        turn_seat=turn_seat,
        rule_config=RuleConfig(player_count=seats, first_seat=turn_seat),
    )


def _bingyi_activating(engine, skill_id: str = JIE_BINGYI) -> bool:
    """停在 ``skill_id`` 的【秉壹】「是否发动」询问上（**还没应答**）。"""
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.SKILL_ACTIVATION
        and str(engine.pending.skill_id) == skill_id
    )


def _park_bingyi(state, skill_id: str = JIE_BINGYI):
    """构造局面 → 开到结束阶段的「是否发动」询问（摸牌阶段置 0 张）。"""
    engine = Engine.from_state(state, seed=1)
    engine.set_draw_phase_count(0, 0)
    return _drive(engine, lambda e: _bingyi_activating(e, skill_id))


def _declare_bingyi(engine) -> None:
    """在【秉壹】的「是否发动」上答「发动」（后续询问 / 摸牌由调用方推进）。"""
    query = engine.pending
    engine.submit(next(a for a in query.legal_actions if isinstance(a, ActivateSkill)))


def _bingyi_seats(engine) -> bool:
    """停在【秉壹】的**座位多选**询问上（`SELECT_OPTIONS`；条件成立时才出现）。"""
    return engine.pending is not None and engine.pending.kind == QueryKind.SELECT_OPTIONS


def _bingyi_turn_over(seat: int = 0):
    """回调：座位 ``seat`` 那一回合**已经走完**（回合指针过到别人）——"整段没有第二问"的观察点。"""

    def stop(engine) -> bool:
        current = engine.state.turn_seat
        return current is not None and int(current) != seat

    return stop


def _bingyi_asked(engine, skill_id: str = JIE_BINGYI) -> list:
    """记录流里该技能的「是否发动」询问（按出现顺序）。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and str(record.query.skill_id) == skill_id
    ]


def _bingyi_seat_queries(engine) -> list:
    """记录流里的 ``SELECT_OPTIONS`` 询问（"条件不成立 ⇒ 没有座位面"的证据）。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_OPTIONS
    ]


def _bingyi_reveals(engine) -> list[tuple[int, int]]:
    """记录流里的 ``card_reveal``（``(座位, 牌 id)``，按出现顺序）。"""
    return [
        (record.seat, record.card.card_id)
        for record in _jie_deltas(engine, "card_reveal")
    ]


def _bingyi_draw_batches(engine) -> list[list[int]]:
    """摸牌落成的 ``card_move`` 记录（``reason = DRAW``；一次 ``draw_for_hand`` 一批）。"""
    return [
        [card.card_id for card in record.cards]
        for record in _jie_deltas(engine, "card_move")
        if record.reason.value == "DRAW"
    ]


def _bingyi_for(hero: str) -> str:
    """武将 id → 那一版的【秉壹】技能 id（同一局面并排钉界版 / OL 版时用）。"""
    return JIE_BINGYI if hero == GUYONG else OL_BINGYI


def _bingyi_run_uniform(mine: tuple[int, ...], *, hero: str = GUYONG) -> tuple:
    """跑完座位 0 的整回合，返回 ``(引擎, 座位面询问或 None)``（不假设条件一定成立）。"""
    skill_id = _bingyi_for(hero)
    engine = _park_bingyi(_bingyi_state(mine=mine, hero=hero), skill_id)
    if not _bingyi_activating(engine, skill_id):
        engine = _drive(engine, _bingyi_turn_over())
        return engine, None
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_turn_over())
    queries = _bingyi_seat_queries(engine)
    return engine, (queries[0] if queries else None)


def test_jie_bingyi_accepts_a_hand_that_is_uniform_in_colour_or_in_category():
    """**界版独有**：同类别、异颜色的两张（都是基本牌）⇒ 成立；标准顾雍同局面 ⇒ 不成立。

    票面 §2 / §3 的 A4：界版的差别**只有条件**一处——「颜色**或类别**均相同」。两条并排跑
    同一局面（同一批牌、同一起始点、同一名 1 号座位），一边出现座位面、一边一张都不摸。
    颜色的那一支同样要成立（OR 不是"改成只看类别"）：同色、异类别的两张两版都成立。
    """
    black = _card_of_color("black", category="basic")
    red = _card_of_color("red", category="basic", exclude=(black,))
    mine = (black, red)
    assert _category_of(black) == _category_of(red) == "basic", "构造：两张同一大类"
    assert color_of_suit(PACK.deck.cards[black].suit) is not color_of_suit(
        PACK.deck.cards[red].suit
    ), "构造：一黑一红（同类别、异颜色）"

    jie, jie_query = _bingyi_run_uniform(mine)
    assert jie_query is not None, "界版：类别全同 ⇒ 条件成立、出座位多选面"
    assert (jie_query.min_selected, jie_query.max_selected) == (1, 2)
    assert sorted(_bingyi_reveals(jie)) == sorted((0, card_id) for card_id in mine), (
        f"展示段照旧逐张亮出：{_bingyi_reveals(jie)}"
    )

    ol, ol_query = _bingyi_run_uniform(mine, hero=OL_GUYONG)
    assert ol_query is None, "标准顾雍：只看颜色 ⇒ 同类别不算「均相同」"
    assert _bingyi_draw_batches(ol) == [], "标准版一张都不摸"
    assert sorted(_bingyi_reveals(ol)) == sorted((0, card_id) for card_id in mine), (
        "条件不成立也要展示（两版同一条读法）"
    )

    red_basic = _card_of_color("red", category="basic")
    red_trick = _card_of_color("red", category="trick", exclude=(red_basic,))
    assert _category_of(red_basic) != _category_of(red_trick), "构造：同色、异类别"
    for hero in (GUYONG, OL_GUYONG):
        _, query = _bingyi_run_uniform((red_basic, red_trick), hero=hero)
        assert query is not None, f"{hero}：颜色全同 ⇒ 成立（颜色的那一支仍在）"


def test_jie_bingyi_rejects_a_hand_uniform_in_neither():
    """颜色、类别**各都不全同** ⇒ 条件不成立：只展示、不出现座位面、一张都不摸。"""
    mine = (
        _card_of_color("red", category="basic"),
        _card_of_color("black", category="trick"),
    )
    assert _category_of(mine[0]) != _category_of(mine[1]), "构造：类别不同"
    assert color_of_suit(PACK.deck.cards[mine[0]].suit) is not color_of_suit(
        PACK.deck.cards[mine[1]].suit
    ), "构造：颜色也不同"

    engine, query = _bingyi_run_uniform(mine)

    assert query is None, "两支都不成立 ⇒ 不构造座位面（edge-cases.md 总纪律 1）"
    assert _bingyi_asked(engine) != [], "「是否发动」照旧先问（展示是发动后无条件发生的）"
    assert sorted(_bingyi_reveals(engine)) == sorted((0, card_id) for card_id in mine)
    assert _bingyi_draw_batches(engine) == [], "一张都不摸"
    assert sorted(engine.state.player(0).hand) == sorted(mine), "手牌一张不动"


def test_jie_bingyi_with_one_hand_card_still_qualifies():
    """1 张手牌「均相同」= **平凡成立**（`04-bingyi-uniform.md` 的 A 裁定，两版共用）。

    ⇒ 名额 N = 1：座位面照出、区间 ``[1, 1]``（含自己），选谁谁摸一张。
    """
    mine = (_card_of_color("red", category="basic"),)
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    assert (query.min_selected, query.max_selected) == (1, 1), "1 张手牌 ⇒ 恰好 1 个名额"
    engine.submit(SelectOptions(choices=("seat:0",), answers_seq=query.seq))
    engine = _drive(engine, _bingyi_turn_over())

    assert len(engine.state.player(0).hand) == 2, "选自己 ⇒ 自己摸一张"
    assert len(engine.state.player(1).hand) == 0, "没选别人 ⇒ 别人不摸"


def test_jie_bingyi_is_not_offered_without_hand_cards():
    """0 张手牌 ⇒ **不给发动入口**（`02-guyong.md` Q3 = A，两版共用）：不问、不展示。

    正面对照放在同一条断言里（防"技能根本不存在"的假绿）：同一夹具、只把 ``mine`` 换成
    一张牌 ⇒ 结束阶段照问。
    """
    with_cards = _park_bingyi(_bingyi_state(mine=(_card_of_color("red"),)))
    assert _bingyi_activating(with_cards), "正面对照：有手牌 ⇒ 结束阶段问「是否发动」"

    empty = Engine.from_state(_bingyi_state(mine=()), seed=1)
    empty.set_draw_phase_count(0, 0)
    empty = _drive(empty, _bingyi_turn_over())

    assert _bingyi_asked(empty) == [], "手牌为空 ⇒ 连「是否发动」都不问（零副作用）"
    assert _bingyi_reveals(empty) == [], "没发动 ⇒ 一张都不亮"
    assert _bingyi_seat_queries(empty) == []


def test_jie_bingyi_seat_cap_equals_the_hand_size():
    """名额上限 N = **手牌数**（不是可选池大小）：3 张手牌、场上 2 人 ⇒ ``max_selected == 3``。"""
    first = _card_of_color("red", category="basic")
    second = _card_of_color("red", category="basic", exclude=(first,))
    third = _card_of_color("red", category="basic", exclude=(first, second))
    mine = (first, second, third)

    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    assert len(engine.state.player(0).hand) == 3, "构造：展示那一刻手牌 3 张"
    assert query.max_selected == 3, "N = 手牌数（不按可选池裁剪）"
    assert len(query.options) == 2, "可选池 = 存活座位（含自己）"


def test_jie_bingyi_each_chosen_seat_draws_one():
    """座位多选面一次交齐两个座位 ⇒ **每个各摸 1 张**（逐座位一次摸牌，不是整次摸 2 张）。"""
    mine = (
        _card_of_color("red", category="basic"),
        _card_of_color("red", category="trick"),
    )
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    assert [str(option) for option in query.options] == ["seat:0", "seat:1"], (
        "可选池 = 全部存活角色（**含自己**），按座位序"
    )
    assert any(isinstance(action, Pass) for action in query.legal_actions), (
        "「至多」⇒ 可以放弃（账本 L775 / L753）"
    )
    engine.submit(SelectOptions(choices=("seat:0", "seat:1"), answers_seq=query.seq))
    engine = _drive(engine, _bingyi_turn_over())

    assert len(engine.state.player(0).hand) == 3, "自己：两张手牌 + 摸的一张"
    assert len(engine.state.player(1).hand) == 1, "另一个座位也摸到一张"
    assert [len(batch) for batch in _bingyi_draw_batches(engine)] == [1, 1], (
        f"逐座位各摸一张（两个独立的摸牌批次）：{_bingyi_draw_batches(engine)}"
    )


def test_jie_bingyi_wording_is_its_own_row():
    """A4：界版有**自己的一行**——数据行、``CATALOG`` 文案、逐步文案、flow 名、描述。

    三处一致性（票面 §3）：数据行声明了第二条技能 ⇒ 加载器要求 ``CATALOG`` 里真有它；
    描述文件另有一行。文案**只差条件那一句**（颜色 ∨ 类别），短 id 表
    ``_REQUIREMENTS`` / ``_STEP_REQUIREMENTS`` 里没有 ``bingyi`` 一行可借 ⇒ 各自逐字写。
    """
    import json
    from pathlib import Path

    from sgs.data import load_heroes
    from sgs.skills import CATALOG, flow_for
    from sgs.skills import phase_body

    table = load_heroes("jie_yijiang_2014_mobile")
    hero = next(hero for hero in table.heroes if hero.id == GUYONG)
    assert [(skill.id, skill.name, skill.skill_type.value) for skill in hero.skills] == [
        ("shenxing", "慎行", "NORMAL"),
        ("bingyi", "秉壹", "NORMAL"),
    ], "票面 §3：数据行追加第二条技能（与 CATALOG 同一次落地）"

    ours = CATALOG[JIE_BINGYI]
    ol = CATALOG[OL_BINGYI]
    assert ours.skill_id != ol.skill_id, "两条 CATALOG 条目（不是一份共享声明）"
    assert ours.carrier.value == "HERO"
    assert ours.skill_type.value == "NORMAL"
    assert ours.locked is False, "牌面写「可以」⇒ 非锁定（派发环先问「是否发动」）"
    assert [hook.timing for hook in ours.hooks] == [Timing.PHASE_FINISH], "结束阶段"
    assert ours.requirement == JIE_BINGYI_FACE, "界版逐字（含「或类别」）"
    assert ol.requirement == OL_BINGYI_FACE, "OL 版逐字（只看颜色）"
    assert ours.requirement != ol.requirement, "A4：同短 id、牌面不同 ⇒ 各自逐字写"

    assert ours.step_requirements and ol.step_requirements, "两版都有逐步文案"
    assert ours.step_requirements is not ol.step_requirements, (
        "A4：各写各的那一份（不共用同一个 dict 对象）"
    )
    assert set(ours.step_requirements) == {"seat"}
    assert ours.step_requirements["seat"].strip()

    assert "bingyi" not in phase_body._REQUIREMENTS, "短 id 表里没有 bingyi 一行可借"
    assert "bingyi" not in phase_body._STEP_REQUIREMENTS

    ours_flow = flow_for(JIE_BINGYI)
    ol_flow = flow_for(OL_BINGYI)
    assert ours_flow is not None and ours_flow.steps == (), "零步：主体全在 effect 里"
    assert ours_flow is not ol_flow, "A4：流水线各自另起"
    assert ours_flow.name == "jie_guyong_bingyi", "界版自己的 flow 名（票面 §3）"
    assert ol_flow.name == "guyong_bingyi"

    path = Path(__file__).resolve().parent.parent / "tools" / "sgs_web" / "descriptions.json"
    descriptions = json.loads(path.read_text(encoding="utf-8"))["skills"]
    assert JIE_BINGYI in descriptions, "本行有自己的 descriptions.json 条目（不借用 OL 那一行）"
    assert OL_BINGYI in descriptions, "OL 那一行照旧在"
    ours_text = descriptions[JIE_BINGYI]
    ol_text = descriptions[OL_BINGYI]
    assert ours_text.startswith(JIE_BINGYI_FACE), "界版描述以界版牌面逐字开头"
    assert ol_text.startswith(OL_BINGYI_FACE), "OL 版描述以 OL 牌面逐字开头"
    assert ours_text != ol_text

    engine = _park_bingyi(_bingyi_state(mine=(_card_of_color("red"),)))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    (query,) = _bingyi_seat_queries(engine)
    assert str(query.skill_id) == f"{JIE_BINGYI}:seat", (
        "座位面挂在**界版**技能 id 上（投影据此查界版那一行逐步文案，A4）"
    )
