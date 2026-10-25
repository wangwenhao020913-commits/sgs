"""E 族：响应窗口张数与帧标签（票 08）。

落点与票 08 §验收一致，全部走**外部缝**：

- ``Engine.from_state`` 的人为局面 + 脚本玩家驱动的**记录流**（响应询问的条数与对象、
  时机与伤害扣减）；
- 帧标签袋的公开小工具 ``add_tag`` / ``has_tag``（票 08 §要做什么 4）。

两条技能的逐字语义（票 08 §要做什么）：

- 吕布【无双】（锁定）：自己使用【杀】/【决斗】时，本次响应所需张数 = **2**——
  【杀】需连续两张【闪】才抵消，【决斗】每次响应需连续两张【杀】；
- 马超【铁骑】（非锁定）：``USE_AFTER_DESIGNATE_TARGET`` 判定，红色则给**该目标**打
  ``UNRESPONSIVE``（逐目标档）⇒ 该目标的响应窗口根本不出现。

【八卦阵】的"视为使用【闪】"仍算**一次**响应（票 08 §要做什么 7 的"视为使用也是使用"），
故它与真牌可以凑齐两张；"每提供者只问一次"的既有口径不变。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.engine.steps import _PER_TARGET_FRAME_KEYS, resolve_advance
from sgs.resolution import (
    USE_TAGS_KEY,
    WHOLE_FIELD_TAGS_KEY,
    Frame,
    Tag,
    UseTags,
    add_tag,
    has_tag,
)
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    PlayCard,
    UseCard,
    WindowCause,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_108")

WUSHUANG = "hero.lvbu.wushuang"
TIEQI = "hero.machao.tieqi"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _judge_deck(judge_card: int, *, used: tuple[int, ...] = ()) -> tuple[int, ...]:
    """把 ``judge_card`` 放在**第 3 张**：摸牌阶段先取走前两张，判定取到的就是它。"""
    excluded = set(used) | {judge_card}
    fillers = [card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded]
    first, second = fillers[0], fillers[1]
    return (first, second, judge_card, *fillers[2:])


def _first_by_color(type_id: str, *, black: bool) -> int:
    from sgs.data import Color, color_of_suit

    wanted = Color.BLACK if black else Color.RED
    return next(
        card_id
        for card_id in _cards(type_id)
        if color_of_suit(PACK.deck.cards[card_id].suit) is wanted
    )


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _use(query, card_id: int, target: int):
    return _find(
        query,
        lambda action: isinstance(action, UseCard)
        and action.card.card_id == card_id
        and action.targets == (target,),
    )


def _queries(engine, kind):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _respond_queries(engine, cause):
    return [
        query
        for query in _queries(engine, "RESPOND")
        if getattr(query, "cause", None) is cause
    ]


def _has_query(engine, kind: str, *, skill_id: str | None = None) -> bool:
    return any(
        skill_id is None or getattr(query, "skill_id", None) == skill_id
        for query in _queries(engine, kind)
    )


def _slash_done(engine, card_id: int) -> bool:
    """那张【杀】的使用链**走完并结算栈清空**（牌已进弃牌堆，没有后续插入结算）。"""
    return card_id in engine.state.discard and not engine.stack


class Scripted:
    """一个座位：先跑 ``handler``；返回 ``None`` 时退到最小合法应答。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return _fallback(query)


def _fallback(query):
    if query.kind == "PLAY_PHASE":
        return _find(query, lambda action: isinstance(action, EndPhase)) or query.legal_actions[0]
    if query.kind in ("DODGE", "RESPOND", "PEACH"):
        return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
    if query.kind == "SKILL_ACTIVATION":
        return _find(query, lambda action: isinstance(action, ActivateSkill)) or (
            _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
        )
    return query.legal_actions[0]


# ---------------------------------------------------------------------------
# ① 无双（【杀】）：两张【闪】才抵消
# ---------------------------------------------------------------------------


def test_wushuang_needs_two_dodges_to_cancel_a_slash():
    """只出一张【闪】⇒ 未被抵消、照常受伤（票 08 §验收 1）。"""
    slash, dodge1, dodge2 = _cards("slash")[0], _cards("dodge")[0], _cards("dodge")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), hero="lvbu"),
            SeatSpec(hand=(dodge1, dodge2)),
        ],
        deck=_deck_excluding(slash, dodge1, dodge2),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, 1)
        return None

    dodges = {"count": 0}

    def defender(query):
        if query.kind == "DODGE":
            dodges["count"] += 1
            if dodges["count"] == 1:
                return _find(query, lambda action: isinstance(action, UseCard))
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(defender)],
        lambda e: _slash_done(e, slash),
    )

    assert len(_queries(engine, "DODGE")) == 2, "无双 ⇒ 需要两次响应"
    assert engine.state.player(1).hp == 3, "只出一张【闪】不抵消，照常受伤"


def test_wushuang_two_dodges_cancel_the_slash():
    """连出两张【闪】⇒ 抵消、不受伤（票 08 §验收 1 的正面对照）。"""
    slash, dodge1, dodge2 = _cards("slash")[0], _cards("dodge")[0], _cards("dodge")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), hero="lvbu"),
            SeatSpec(hand=(dodge1, dodge2)),
        ],
        deck=_deck_excluding(slash, dodge1, dodge2),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, 1)
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(defender)],
        lambda e: _slash_done(e, slash),
    )

    assert len(_queries(engine, "DODGE")) == 2, "两张【闪】= 两次响应"
    assert engine.state.player(1).hp == 4, "两张都成功 ⇒ 抵消"


# ---------------------------------------------------------------------------
# ② 无双（【决斗】）：每次响应两张【杀】
# ---------------------------------------------------------------------------


def test_wushuang_duel_needs_two_slashes_for_one_response():
    """只打出一张【杀】⇒ 未满足响应、该方受伤（票 08 §验收 2）。"""
    duel, first, second = _cards("duel")[0], _cards("slash")[0], _cards("slash")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(duel,), hero="lvbu"),
            SeatSpec(hand=(first, second)),
        ],
        deck=_deck_excluding(duel, first, second),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, duel, 1)
        return None

    plays = {"count": 0}

    def defender(query):
        if query.kind == "RESPOND" and query.cause is WindowCause.DUEL:
            plays["count"] += 1
            if plays["count"] == 1:
                return _find(query, lambda action: isinstance(action, PlayCard))
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(defender)],
        lambda e: e.state.player(1).hp < 4,
    )

    assert len(_respond_queries(engine, WindowCause.DUEL)) == 2, "一次响应要问两张【杀】"
    assert engine.state.player(1).hp == 3, "只出一张【杀】不满足响应，该方受伤"


def test_wushuang_duel_a_full_pair_hands_the_response_to_the_other_side():
    """连出两张【杀】⇒ 这一次响应满足，轮到对方（票 08 §验收 2 的正面对照）。"""
    duel, first, second = _cards("duel")[0], _cards("slash")[0], _cards("slash")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(duel,), hero="lvbu"),
            SeatSpec(hand=(first, second)),
        ],
        deck=_deck_excluding(duel, first, second),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, duel, 1)
        if query.kind == "RESPOND" and query.cause is WindowCause.DUEL:
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    def defender(query):
        if query.kind == "RESPOND" and query.cause is WindowCause.DUEL:
            return _find(query, lambda action: isinstance(action, PlayCard))
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(defender)],
        lambda e: e.state.player(0).hp < 4,
    )

    assert engine.state.player(1).hp == 4, "两张【杀】已满足该方这一次响应"
    assert engine.state.player(0).hp == 3, "响应轮到对方；对方放弃则对方受伤"


# ---------------------------------------------------------------------------
# ②b 无双（【决斗】的反向）：吕布**成为**别人的【决斗】目标时，加倍的是**对手**
# ---------------------------------------------------------------------------


def test_wushuang_duel_makes_lvbus_opponent_respond_with_two_slashes():
    """吕布**被**【决斗】⇒ 吕布本人一张、**对手**（这次是使用者）每次两张（票 23 用户裁定）。

    牌面 ② 的"该角色"= **跟吕布决斗的那一方**——标准版牌面把这一半直接写成"与你进行
    【决斗】的角色每次需连续打出两张【杀】"。故吕布挨【决斗】时**不**加倍：他打出一张
    就算通过，加倍落在对手身上。

    判据：吕布打出一张【杀】后响应轮转到使用者，使用者必须连出**两张**；只出一张即不满足，
    由【决斗】判负受伤（正是本用例断言的"两张"）。
    """
    duel, first, second = _cards("duel")[0], _cards("slash")[0], _cards("slash")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(duel, second)),
            SeatSpec(hand=(first,), hero="lvbu"),
        ],
        deck=_deck_excluding(duel, first, second),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, duel, 1)
        if query.kind == "RESPOND" and query.cause is WindowCause.DUEL:
            # 座位 0 = 使用者：这次响应只打一张，看它算不算"满足两次"。
            return _find(query, lambda action: isinstance(action, PlayCard))
        return None

    def defender(query):
        if query.kind == "RESPOND" and query.cause is WindowCause.DUEL:
            return _find(query, lambda action: isinstance(action, PlayCard))
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(defender)],
        lambda e: e.state.player(0).hp < 4 or e.state.player(1).hp < 4,
    )

    asks = _respond_queries(engine, WindowCause.DUEL)
    assert [query.actor for query in asks] == [1, 0, 0], (
        "吕布先响应（**一张**即可，1 问），随后对手这次响应要问**两张**（2 问）"
    )
    assert engine.state.player(0).hp == 3, "对手只出一张【杀】不满足响应 ⇒ 对手受伤"
    assert engine.state.player(1).hp == 4, "吕布一张就够了（无双不惩罚自己）"


# ---------------------------------------------------------------------------
# ③ 无双与提供者共存：【八卦阵】红算一张，两张可"真牌 + 八卦阵"
# ---------------------------------------------------------------------------


EIGHT_DIAGRAMS = _cards("eight_diagrams")[0]


def _wushuang_provider_state(dodge: int | None, judge_card: int):
    slash = _cards("slash")[0]
    hand = () if dodge is None else (dodge,)
    used = (slash, EIGHT_DIAGRAMS, *hand)
    return (
        slash,
        build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=(slash,), hero="lvbu"),
                SeatSpec(hand=hand, equip=(EIGHT_DIAGRAMS,)),
            ],
            deck=_judge_deck(judge_card, used=used),
            turn_seat=0,
        ),
    )


def test_wushuang_a_red_eight_diagrams_counts_as_one_of_the_two_dodges():
    """【八卦阵】判定为红算作**一张**【闪】；两张的来源可以是"真牌 + 八卦阵"（票 08 §验收 3）。"""
    red_judge = _first_by_color("peach", black=False)
    slash, state = _wushuang_provider_state(_cards("dodge")[0], red_judge)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, 1)
        return None

    def defender(query):
        if query.kind == "DODGE":
            skill = _find(query, lambda action: isinstance(action, ActivateSkill))
            if skill is not None:
                return skill
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(defender)],
        lambda e: _slash_done(e, slash),
    )

    dodges = _queries(engine, "DODGE")
    assert len(dodges) == 2, "两张【闪】：八卦阵一张 + 真牌一张"
    assert any(isinstance(action, ActivateSkill) for action in dodges[0].legal_actions)
    assert not any(isinstance(action, ActivateSkill) for action in dodges[1].legal_actions), (
        "每提供者只问一次：第二张不再给【八卦阵】"
    )
    assert engine.state.player(1).hp == 4, "两张都成功 ⇒ 抵消"


def test_wushuang_a_black_eight_diagrams_does_not_count():
    """判定为黑 ⇒ 该提供者机会用尽、只算零张；真牌只够一张 ⇒ 照常受伤。"""
    black_judge = _first_by_color("slash", black=True)
    slash, state = _wushuang_provider_state(_cards("dodge")[0], black_judge)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, 1)
        return None

    def defender(query):
        if query.kind == "DODGE":
            skill = _find(query, lambda action: isinstance(action, ActivateSkill))
            if skill is not None:
                return skill
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(defender)],
        lambda e: _slash_done(e, slash),
    )

    assert len(_queries(engine, "DODGE")) == 3, "黑 ⇒ 重问真牌窗口；真牌只有一张，再问一次"
    assert engine.state.player(1).hp == 3, "黑判定不算一张，真牌凑不齐两张 ⇒ 受伤"


# ---------------------------------------------------------------------------
# ④ 铁骑：判定为红 ⇒ 该目标的响应窗口根本不出现
# ---------------------------------------------------------------------------


def _tieqi_state(judge_card: int, *, extra_hand: tuple[int, ...] = (), extra_seats=()):
    slash = _cards("slash")[0]
    used = (slash, *extra_hand)
    return (
        slash,
        build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=(slash,), hero="machao"),
                SeatSpec(hand=extra_hand),
                *extra_seats,
            ],
            deck=_judge_deck(judge_card, used=used),
            turn_seat=0,
        ),
    )


def _activate_on_skill(query):
    if query.kind == "SKILL_ACTIVATION":
        return _find(query, lambda action: isinstance(action, ActivateSkill))
    return None


def test_tieqi_red_hides_the_whole_dodge_window():
    """判定为红 ⇒ 该目标的【闪】窗口**根本不出现**（记录流里没有该目标的询问）（票 08 §验收 4）。"""
    red_judge = _first_by_color("peach", black=False)
    slash, state = _tieqi_state(red_judge)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, 1)
        return _activate_on_skill(query)

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted()],
        lambda e: _slash_done(e, slash),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id=TIEQI), "铁骑先问是否发动"
    assert _queries(engine, "DODGE") == [], "被判红的目标没有任何【闪】询问"
    assert engine.state.player(1).hp == 3, "不可响应 ⇒ 照常受伤"


def test_tieqi_black_leaves_the_window_open():
    """判定非红 ⇒ 窗口照常（票 08 §验收 4 的反面对照）。"""
    black_judge = _first_by_color("slash", black=True)
    dodge = _cards("dodge")[0]
    slash, state = _tieqi_state(black_judge, extra_hand=(dodge,))

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, 1)
        return _activate_on_skill(query)

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(defender)],
        lambda e: _slash_done(e, slash),
    )

    assert len(_queries(engine, "DODGE")) == 1, "判定非红 ⇒ 窗口照常出现"
    assert engine.state.player(1).hp == 4, "【闪】照常抵消"


# ---------------------------------------------------------------------------
# ⑤ 铁骑 + 八卦阵 / 多目标逐目标档 / 不跨使用
# ---------------------------------------------------------------------------


def _advance(engine, players, stop):
    """逐步推进到 ``stop(engine)`` 成立（同 ``tests/test_hero_move_after.py`` 的手写推进）。"""
    for _ in range(20_000):
        if stop(engine):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _at_play_phase(engine, players):
    """跑到**出牌阶段开始**（摸牌阶段已过完），此刻种下手牌才是最精确的时点。"""
    return _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
            for record in e.records
        ),
    )


def _seed_hand(engine, seat: int, cards: tuple[int, ...]) -> None:
    """把测试要用的手牌**换成** ``cards``（构造局面的入口，不新增领域机制）。"""
    hand = engine.state.player(seat).hand
    hand.clear()
    hand.extend(cards)


def _two_judge_deck(first: int, second: int, *, used: tuple[int, ...]) -> tuple[int, ...]:
    """前两张给摸牌阶段，第 3 / 4 张依次给两次判定。"""
    excluded = set(used) | {first, second}
    fillers = [card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded]
    return (fillers[0], fillers[1], first, second, *fillers[2:])


def test_tieqi_red_also_seals_the_eight_diagrams_provider():
    """铁骑封"视为使用【闪】"：被封的目标拿不到【八卦阵】候选（票 08 §验收 5）。"""
    red_judge = _first_by_color("peach", black=False)
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), hero="machao"),
            SeatSpec(equip=(EIGHT_DIAGRAMS,)),
        ],
        deck=_judge_deck(red_judge, used=(slash, EIGHT_DIAGRAMS)),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, 1)
        return _activate_on_skill(query)

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted()],
        lambda e: _slash_done(e, slash),
    )

    skills = [
        query.skill_id
        for query in _queries(engine, "SKILL_ACTIVATION")
    ]
    assert TIEQI in skills, "铁骑自己照常问"
    assert "equip.eight_diagrams" not in skills, "窗口被跳过 ⇒ 提供者不进候选、不发询问"
    assert _queries(engine, "DODGE") == []


def test_tieqi_multi_target_tag_stays_on_the_judged_target_only():
    """【方天画戟】多目标：判定为红的目标窗口消失，另一个照常（票 08 §验收 4 的逐目标判据）。"""
    slash = _cards("slash")[0]
    halberd = _cards("halberd")[0]
    red_judge = _first_by_color("peach", black=False)
    black_judge = _first_by_color("slash", black=True)

    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(halberd,), hero="machao"),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_two_judge_deck(red_judge, black_judge, used=(slash, halberd)),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == slash
                and action.targets == (1, 2),
            )
        return _activate_on_skill(query)

    players = [Scripted(attacker), Scripted(), Scripted()]
    engine = _at_play_phase(Engine.from_state(state, seed=1), players)
    _seed_hand(engine, 0, (slash,))
    _advance(engine, players, lambda e: _slash_done(e, slash))

    assert [query.actor for query in _queries(engine, "DODGE")] == [2], (
        "只有没被判红的座位 2 有【闪】窗口；座位 1 的窗口根本不出现"
    )
    assert engine.state.player(1).hp == 3, "不可响应 ⇒ 照常受伤"
    assert engine.state.player(2).hp == 3, "放弃响应 ⇒ 照常受伤"


def test_tieqi_tag_does_not_cross_into_the_next_use():
    """标签随本次使用消失：下一次使用同一张牌时窗口照常出现（票 08 §验收 7）。"""
    first_slash, second_slash = _cards("slash")[0], _cards("slash")[1]
    crossbow = _cards("crossbow")[0]
    red_judge = _first_by_color("peach", black=False)
    black_judge = _first_by_color("slash", black=True)

    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(crossbow,), hero="machao"),
            SeatSpec(),
        ],
        deck=_two_judge_deck(
            red_judge, black_judge, used=(first_slash, second_slash, crossbow)
        ),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, first_slash, 1) or _use(query, second_slash, 1)
        return _activate_on_skill(query)

    players = [Scripted(attacker), Scripted()]
    engine = _at_play_phase(Engine.from_state(state, seed=1), players)
    _seed_hand(engine, 0, (first_slash, second_slash))
    _advance(
        engine,
        players,
        lambda e: first_slash in e.state.discard
        and second_slash in e.state.discard
        and not e.stack,
    )

    dodges = _queries(engine, "DODGE")
    assert len(dodges) == 1, "第一次被判红（无窗口），第二次判定非红 ⇒ 窗口照常出现一次"
    assert dodges[0].actor == 1
    assert engine.state.player(1).hp == 2, "两次【杀】都命中（第一次不可响应、第二次放弃响应）"


# ---------------------------------------------------------------------------
# ⑥ 帧标签袋本身：两档、值不可变、顺序确定
# ---------------------------------------------------------------------------


def test_the_frame_tag_bag_has_two_tiers_and_immutable_values():
    """``add_tag`` / ``has_tag`` 的公开语义（票 08 §要做什么 4）：两档互不串味、值不可变。"""
    frame = Frame(kind=FrameKind.RESOLVE)
    before = frame.data.get(USE_TAGS_KEY)

    add_tag(frame, Tag.UNRESPONSIVE, target=1)
    assert has_tag(frame, Tag.UNRESPONSIVE, target=1)
    assert not has_tag(frame, Tag.UNRESPONSIVE, target=2), "逐目标档不作用于别的目标"
    assert not has_tag(frame, Tag.UNRESPONSIVE), "逐目标档不是全场档"
    assert before is None, "袋是按需造的，不动裸帧"

    add_tag(frame, Tag.PREVENTED)
    assert has_tag(frame, Tag.PREVENTED)
    assert has_tag(frame, Tag.PREVENTED, target=2), "全场档对任何一个目标都成立"

    first = frame.data[WHOLE_FIELD_TAGS_KEY]
    assert isinstance(first, UseTags)
    add_tag(frame, Tag.SAVED)
    assert first is not frame.data[WHOLE_FIELD_TAGS_KEY], "加入标签换一个新值（浅拷贝安全）"
    assert not first.has(Tag.SAVED), "旧值不被就地改写"


def test_the_frame_tag_bag_is_idempotent_and_keeps_insertion_order():
    """同一标签重复加入不改值、不重排（迭代顺序确定，不用集合；票 08 / 源码纪律 §3）。"""
    frame = Frame(kind=FrameKind.RESOLVE)
    add_tag(frame, Tag.UNRESPONSIVE, target=2)
    add_tag(frame, Tag.PREVENTED)
    add_tag(frame, Tag.UNRESPONSIVE, target=2)

    assert frame.data[USE_TAGS_KEY].target_tags == ((2, Tag.UNRESPONSIVE),)
    assert frame.data[WHOLE_FIELD_TAGS_KEY].global_tags == (Tag.PREVENTED,)


def test_the_whole_field_tag_survives_a_per_target_cursor_advance():
    """R4-Q1：只把**逐目标档**登记进 ``_PER_TARGET_FRAME_KEYS``（code-review C3）。

    多目标【杀】里游标推进必须清掉逐目标档（否则标签会串到下一个目标），但**全场档**
    （【寒冰剑】的 ``PREVENTED`` / 【贯石斧】的 ``SAVED`` 一类）作用于整次使用，必须跨
    目标存活。两档因此分住两个 ``frame.data`` 键，只有逐目标那个登记进清除表。
    """
    assert USE_TAGS_KEY in _PER_TARGET_FRAME_KEYS, "逐目标档随游标推进清除"
    assert WHOLE_FIELD_TAGS_KEY not in _PER_TARGET_FRAME_KEYS, "全场档不随游标推进清除"

    frame = Frame(kind=FrameKind.RESOLVE, data={"targets": (1, 2), "cursor": 0})
    add_tag(frame, Tag.PREVENTED)  # 全场档
    add_tag(frame, Tag.UNRESPONSIVE, target=1)  # 逐目标档

    resolve_advance(None, frame)

    assert frame.data["cursor"] == 1, "游标真的推进了（断言不空转）"
    assert has_tag(frame, Tag.PREVENTED, target=2), "全场档跨目标存活"
    assert not has_tag(frame, Tag.UNRESPONSIVE, target=1), "逐目标档随游标推进被清除"

