"""M5-06：【火攻】——亮牌 / 同花色弃牌 / 火焰伤害（spec D10 / §5.1.4 / §6 A1）。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + ``legal_actions_for`` 的候选面
（第 0 层目标过滤）+ 记录流（``card_reveal`` 公开 delta / ``card_move`` / ``hp_change``）
+ ``_FrameSpy``（伤害帧私有事实 ``source`` / ``nature`` / ``channel``）+ 投影（``project``）。

钉住的合同（D10 / §5.1.4）：

1. 目标须**有手牌**才能被指定（新 target filter），**可以指定自己**；无手牌者不在候选内；
2. 结算时目标没有手牌（例：仅剩一张火攻时对自己使用）⇒ 无亮牌步、无伤害，火攻进弃牌堆；
3. 目标亮 **1 张**手牌（目标自己选），亮牌是**公开事实**（``card_reveal`` delta），
   牌仍留在手里；未亮出的手牌不进任何公开 delta、投影里只有张数；
4. 使用者可以弃一张**同花色**手牌造成 1 点火焰伤害（来源 = 使用者、渠道 = 火攻本体；
   进入统一伤害管线）；**即使有同花色也可以放弃**；没有同花色 ⇒ 只能放弃；
5. 亮牌不持久：效果结束后手牌区没有「已知」残留，只有那一条公开记录。

军争牌只在 ``standard_ex_160`` 档里，故每个用例都显式选这一档（档位纪律）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sgs.data import Suit, load_content_pack
from sgs.effects import DamageNature, check_targets, legal_actions_for
from sgs.engine import Engine, dump_journal, read_journal
from sgs.state import (
    EndPhase,
    FrameKind,
    Pass,
    QueryKind,
    SelectCards,
    UseCard,
    WindowCause,
    project,
)

from support import SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_ex_160")

FIRE_ATTACK = cards_of_type(PACK, "fire_attack")
NULLIFY = cards_of_type(PACK, "nullify")
FA = FIRE_ATTACK[0]
"""本票的渠道牌：一张具体的【火攻】。"""


def _cards_with_suit(suit: Suit, *, exclude: tuple[int, ...] = ()) -> list[int]:
    """牌堆里该花色的全部 ``card_id``（数据表序）。"""
    excluded = set(exclude)
    return [
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit is suit and index not in excluded
    ]


HEARTS = _cards_with_suit(Suit.HEART, exclude=(FA,))
SPADES = _cards_with_suit(Suit.SPADE, exclude=(FA,))
CLUBS = _cards_with_suit(Suit.CLUB, exclude=(FA,))

EQUIPMENT = [
    cards_of_type(PACK, "chitu")[0],
    cards_of_type(PACK, "jueying")[0],
]
"""摸牌阶段可控的两张装备：出牌阶段把它们置入装备区即可把**手牌**清空。"""


def _deck_excluding(*worst: int) -> tuple[int, ...]:
    excluded = set(worst)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


# ---------------------------------------------------------------------------
# 驱动：脚本参与者 + 逐步推进（与 tests/test_m5_wine.py 同姿态）
# ---------------------------------------------------------------------------


def _minimal(query):
    """最小合法应答：出牌阶段结束、响应 / 濒死放弃、其余取第一个候选。"""
    if query.kind == QueryKind.PLAY_PHASE:
        return next(
            action for action in query.legal_actions if isinstance(action, EndPhase)
        )
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _Scripted:
    """一个座位：先跑 ``handler``，返回 ``None`` 时退到最小应答；录下每一次询问。"""

    def __init__(self, handler=None) -> None:
        self.handler = handler
        self.queries: list[Any] = []

    def respond(self, query):
        self.queries.append(query)
        if self.handler is not None:
            action = self.handler(query)
            if action is not None:
                return action
        return _minimal(query)


def _use_fire_attack(query, *, target: int):
    """出牌阶段：从**候选**里取"用火攻打 ``target``"那条（不手搓动作）。"""
    for action in query.legal_actions:
        if (
            isinstance(action, UseCard)
            and action.card.card_id == FA
            and action.targets == (target,)
        ):
            return action
    return None


def _equip_from_candidate(query, card_id: int):
    """出牌阶段：从**候选**里取"把 ``card_id`` 置入自己装备区"那条。"""
    for action in query.legal_actions:
        if (
            isinstance(action, UseCard)
            and action.card.card_id == card_id
            and action.targets == (0,)
        ):
            return action
    return None


def _pick_card(query, card_id: int):
    """从候选里取"选中 ``card_id`` 那张单张"的那条（AI 口径：只读 legal_actions）。"""
    for action in query.legal_actions:
        if isinstance(action, SelectCards) and len(action.picks) == 1:
            slot = query.slots[action.picks[0]]
            if slot.card is not None and slot.card.card_id == card_id:
                return action
    return None


def _queries(engine, kind: QueryKind) -> list[Any]:
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind.value
    ]


def _deltas(engine, delta_type: str) -> list[Any]:
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def _hp_changes(engine) -> list[tuple[int, int, int]]:
    return [
        (record.seat, record.before, record.after)
        for record in _deltas(engine, "hp_change")
    ]


class _FrameSpy(Engine):
    """记下每一次压栈的帧（类型 / frame_id / 载荷）：伤害帧私有事实只能这样观测。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pushed: list[tuple[FrameKind, int, dict[str, Any]]] = []

    def push_frame(self, kind, data, *, timing):  # type: ignore[no-untyped-def]
        frame = super().push_frame(kind, data, timing=timing)
        self.pushed.append((kind, frame.frame_id, dict(data)))
        return frame

    def damage_frames(self) -> list[dict[str, Any]]:
        return [data for kind, _fid, data in self.pushed if kind is FrameKind.DAMAGE]


def _two_seat_engine(
    *,
    user_hand: tuple[int, ...],
    target_hand: tuple[int, ...],
    target_equip: tuple[int, ...] = (),
    top: tuple[int, ...] = (),
    spy: bool = False,
):
    """座位 0 = 火攻使用者、座位 1 = 目标。

    ``top`` = 牌堆顶顺序（摸牌阶段会摸走前两张；测试用它钉住"摸到什么"，使
    "手牌里有没有同花色"不受洗牌 / 牌堆顺序影响）。其后的牌堆 = 其余全部牌。
    """
    used = (*user_hand, *target_hand, *target_equip, *top)
    deck = (*top, *_deck_excluding(*used))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hand=user_hand),
            SeatSpec(hp=4, hand=target_hand, equip=target_equip),
        ],
        deck=deck,
        turn_seat=0,
        phase=None,
    )
    engine_cls = _FrameSpy if spy else Engine
    return engine_cls.from_state(state, seed=1)


# ---------------------------------------------------------------------------
# 1. 目标过滤：无手牌不可指定；有手牌可指定；可指定自己
# ---------------------------------------------------------------------------


def _fire_attack_targets(state, seat: int = 0) -> tuple[int, ...]:
    return tuple(
        action.targets[0]
        for action in legal_actions_for(
            state, seat, QueryKind.PLAY_PHASE, answers_seq=1
        )
        if isinstance(action, UseCard) and action.card.card_id == FA
    )


def test_fire_attack_cannot_target_a_character_without_hand_cards():
    """第 0 层目标过滤：空手牌者（含自己）不进候选；有手牌者进（成对反例）。"""
    empty = _two_seat_engine(user_hand=(FA,), target_hand=())
    assert 1 not in _fire_attack_targets(empty.state), "目标无手牌 ⇒ 不能指定"
    assert check_targets(empty.state, 0, empty.state.card(FA)) == (0,), (
        "使用者自己手里有这张【火攻】⇒ 自己仍合法（D10 可指定自己）"
    )

    full = _two_seat_engine(user_hand=(FA,), target_hand=(SPADES[0],))
    assert _fire_attack_targets(full.state) == (0, 1), "目标有手牌 ⇒ 可以指定"
    assert check_targets(full.state, 0, full.state.card(FA)) == (0, 1)


def test_fire_attack_may_target_itself_when_the_user_holds_other_hand_cards():
    """火攻可指定自己（D10）：自己手里除【火攻】外还有牌 ⇒ 自己在候选内。"""
    engine = _two_seat_engine(user_hand=(FA, HEARTS[0]), target_hand=(SPADES[0],))
    assert _fire_attack_targets(engine.state) == (0, 1)

    # 自己只有这张【火攻】：指定当时手牌非空（这张火攻本身也算）⇒ 自己在候选内。
    only = _two_seat_engine(user_hand=(FA,), target_hand=(SPADES[0],))
    assert _fire_attack_targets(only.state) == (0, 1)


# ---------------------------------------------------------------------------
# 2. 结算驱动：出牌阶段用火攻（目标 = 1），目标亮牌，使用者决定弃 / 不弃
# ---------------------------------------------------------------------------


class _Scenario:
    """一张【火攻】的完整脚本：使用者 / 目标两个座位的应答（全部取自 ``legal_actions``）。"""

    def __init__(
        self,
        *,
        target: int = 1,
        reveal: int | None = None,
        discard: int | None = None,
        nullify: int | None = None,
        pre_play: tuple[int, ...] = (),
    ) -> None:
        self.target_seat = target
        self.reveal = reveal
        self.discard = discard
        self.nullify = nullify
        self.pre_play = list(pre_play)

    def _nullify_or_pass(self, query):
        if query.kind == QueryKind.RESPOND and query.cause == WindowCause.NULLIFY:
            if self.nullify is not None:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.card_id == self.nullify:
                        self.nullify = None
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        return None

    def user(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            if self.pre_play:
                action = _equip_from_candidate(query, self.pre_play[0])
                assert action is not None, f"装备候选里没有 {self.pre_play[0]}"
                self.pre_play.pop(0)
                return action
            action = _use_fire_attack(query, target=self.target_seat)
            return action if action is not None else _minimal(query)
        control = self._nullify_or_pass(query)
        if control is not None:
            return control
        if query.kind == QueryKind.SELECT_CARDS:
            assert query.actor == 0
            if self.discard is None:
                return next(
                    action for action in query.legal_actions if isinstance(action, Pass)
                )
            action = _pick_card(query, self.discard)
            assert action is not None, f"同花色弃牌候选里没有 {self.discard}"
            return action
        return _minimal(query)

    def target(self, query):
        control = self._nullify_or_pass(query)
        if control is not None:
            return control
        if query.kind == QueryKind.SELECT_CARDS:
            assert query.actor == self.target_seat
            if self.reveal is None:
                return _minimal(query)
            action = _pick_card(query, self.reveal)
            assert action is not None, f"亮牌候选里没有 {self.reveal}"
            return action
        return _minimal(query)


def _play_fire_attack(engine, scenario: _Scenario):
    """跑到这次【火攻】完整结算（本体进弃牌堆）为止；返回两个座位的脚本参与者。

    在"火攻进弃牌堆"后的**下一次询问**处停下，故目标还没有开始自己的回合（没摸牌），
    手牌 / 体力就是这次结算后的原样。
    """
    user = _Scripted(scenario.user)
    target = _Scripted(scenario.target)
    run_until(engine, [user, target], lambda e: FA in e.state.discard)
    return user, target


def _select_queries(scripted) -> list[Any]:
    return [query for query in scripted.queries if query.kind == QueryKind.SELECT_CARDS]


# ---------------------------------------------------------------------------
# 3. 亮 1 张是公开事实；未亮出的手牌不泄露；牌不离手
# ---------------------------------------------------------------------------


def test_fire_attack_reveals_exactly_one_card_as_a_public_record():
    engine = _two_seat_engine(
        user_hand=(FA,),
        target_hand=(SPADES[0], HEARTS[0], CLUBS[0]),
        top=(CLUBS[1], CLUBS[2]),
        spy=True,
    )
    before = tuple(engine.state.player(1).hand)
    user, target = _play_fire_attack(
        engine, _Scenario(target=1, reveal=SPADES[0], discard=None)
    )

    reveal_queries = _select_queries(target)
    assert len(reveal_queries) == 1, "目标只被问一次：亮哪一张"
    assert reveal_queries[0].actor == 1, "亮牌的选择权在**目标**自己"
    assert _select_queries(user) == [], "使用者没有同花色牌 ⇒ 不问他"

    reveals = _deltas(engine, "card_reveal")
    assert len(reveals) == 1, "亮牌恰好一条公开记录"
    reveal = reveals[0]
    assert (reveal.seat, reveal.card.card_id) == (1, SPADES[0])
    assert Suit(reveal.card.suit) is Suit.SPADE
    assert reveal.frame_id == _resolution_frame_id(engine), "公开记录属于这次结算帧"

    assert tuple(engine.state.player(1).hand) == before, "亮牌不搬牌：手牌原样留在手里"

    # 未亮出的两张牌不进任何公开 delta；投影里对手手牌只有张数。
    hidden = {HEARTS[0], CLUBS[0]}
    names = {
        card.card_id
        for record in _deltas(engine, "card_move")
        for card in record.cards
    }
    assert not (hidden & names), "未亮出的手牌一张都不进公开记录"
    view = project(engine.state, 0)
    assert view.players[1].hand_count == 3, "对手手牌只以**张数**公开"
    assert {card.card_id for card in view.self_hand}.isdisjoint(hidden)

    assert engine.state.player(1).hp == 4
    assert engine.damage_frames() == []
    assert FA in engine.state.discard


def test_the_public_reveal_round_trips_through_the_journal(tmp_path: Path):
    engine = _two_seat_engine(
        user_hand=(FA,),
        target_hand=(SPADES[0], HEARTS[0]),
        top=(CLUBS[0], CLUBS[1]),
        spy=True,
    )
    _play_fire_attack(engine, _Scenario(target=1, reveal=SPADES[0], discard=None))

    path = tmp_path / "fire-attack.jsonl"
    path.write_text(engine.journal(), encoding="utf-8")
    records = read_journal(path)

    reveals = [
        record
        for record in records
        if record.kind == "delta" and record.delta_type == "card_reveal"
    ]
    assert len(reveals) == 1
    assert reveals[0].card.card_id == SPADES[0]
    assert dump_journal(records) == engine.journal(), "公开亮牌可重放"


def _resolution_frame_id(engine) -> int:
    """这次【火攻】的 RESOLVE 帧 id（记录流里第一个 RESOLVE 帧）。"""
    return next(
        record.frame_id
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.RESOLVE
    )


def test_fire_attack_resolution_without_hand_cards_does_nothing():
    """结算时目标没有手牌（仅剩一张火攻时对自己用）⇒ 无亮牌、无伤害、火攻进弃牌堆。"""
    engine = _two_seat_engine(
        user_hand=(FA,),
        target_hand=(SPADES[0],),
        top=tuple(EQUIPMENT),
        spy=True,
    )
    # 摸牌阶段的 2 张装备先置入装备区 ⇒ 出牌时手里**只剩这张火攻**；对自己使用后
    # 结算时手牌为空（正是票面举的例子）。
    user, target_script = _play_fire_attack(
        engine, _Scenario(target=0, pre_play=tuple(EQUIPMENT))
    )

    assert _select_queries(user) == [], "没有手牌 ⇒ 不压亮牌步、也不问同花色"
    assert _select_queries(target_script) == []
    assert _deltas(engine, "card_reveal") == []
    assert engine.damage_frames() == []
    assert [player.hp for player in engine.state.players] == [4, 4]
    assert engine.state.player(0).hand == [], "那张火攻已在处理区 → 弃牌堆"
    assert FA in engine.state.discard, "无事发生，但火攻本体进弃牌堆"


def test_a_nullified_fire_attack_does_not_reveal_or_damage():
    engine = _two_seat_engine(
        user_hand=(FA, SPADES[1]),
        target_hand=(SPADES[0], NULLIFY[0]),
        top=(CLUBS[0], CLUBS[1]),
        spy=True,
    )
    _play_fire_attack(
        engine,
        _Scenario(target=1, reveal=SPADES[0], discard=SPADES[1], nullify=NULLIFY[0]),
    )

    assert _deltas(engine, "card_reveal") == [], "被无懈 ⇒ 不亮牌"
    assert engine.damage_frames() == []
    assert [player.hp for player in engine.state.players] == [4, 4]
    assert FA in engine.state.discard
    assert NULLIFY[0] in engine.state.discard


# ---------------------------------------------------------------------------
# 4. 同花色弃牌 → 1 点火焰伤害；放弃 / 无同花色 → 0
# ---------------------------------------------------------------------------


def test_fire_attack_discarding_a_same_suit_card_deals_one_fire_damage():
    engine = _two_seat_engine(
        user_hand=(FA, SPADES[1]),
        target_hand=(SPADES[0], CLUBS[0]),
        top=(CLUBS[1], CLUBS[2]),
        spy=True,
    )
    _play_fire_attack(engine, _Scenario(target=1, reveal=SPADES[0], discard=SPADES[1]))

    frames = engine.damage_frames()
    assert len(frames) == 1
    damage = frames[0]
    assert damage["source"] == 0, "伤害来源 = 火攻使用者"
    assert damage["target"] == 1
    assert damage["amount"] == 1
    assert damage["nature"] is DamageNature.FIRE, "属性 = 火焰"
    assert damage["channel"].card_id == FA, "渠道牌 = 火攻本体"

    assert [player.hp for player in engine.state.players] == [4, 3]
    assert _hp_changes(engine) == [(1, 4, 3)]
    assert SPADES[1] in engine.state.discard, "同花色手牌弃置进弃牌堆"
    assert FA in engine.state.discard
    assert tuple(engine.state.player(1).hand) == (SPADES[0], CLUBS[0])


def test_fire_attack_user_may_pass_even_with_a_same_suit_card():
    engine = _two_seat_engine(
        user_hand=(FA, SPADES[1], HEARTS[0]),
        target_hand=(SPADES[0],),
        top=(CLUBS[0], CLUBS[1]),
        spy=True,
    )
    user, _target = _play_fire_attack(
        engine, _Scenario(target=1, reveal=SPADES[0], discard=None)
    )

    discard_queries = _select_queries(user)
    assert len(discard_queries) == 1
    query = discard_queries[0]
    assert query.spec.optional is True, "同花色弃牌是可放弃的"
    assert any(isinstance(action, Pass) for action in query.legal_actions), (
        "即使有同花色，候选里也有 Pass"
    )
    assert {
        action.picks
        for action in query.legal_actions
        if isinstance(action, SelectCards)
    } == {(0,)}, "同花色手牌恰好一张"
    assert Suit(query.slots[0].card.suit) is Suit.SPADE

    assert engine.damage_frames() == [], "放弃 ⇒ 0 伤害"
    assert [player.hp for player in engine.state.players] == [4, 4]
    assert tuple(engine.state.player(0).hand) == (SPADES[1], HEARTS[0], CLUBS[0], CLUBS[1]), (
        "放弃不弃牌（后两张是摸牌阶段摸到的）"
    )
    assert FA in engine.state.discard


def test_fire_attack_without_a_same_suit_card_can_only_pass():
    engine = _two_seat_engine(
        user_hand=(FA, HEARTS[0], CLUBS[0]),
        target_hand=(SPADES[0],),
        top=(CLUBS[1], CLUBS[2]),
        spy=True,
    )
    user, _target = _play_fire_attack(
        engine, _Scenario(target=1, reveal=SPADES[0], discard=None)
    )

    assert _select_queries(user) == [], "没有同花色 ⇒ 选择面为空（只能放弃）"
    assert engine.damage_frames() == []
    assert [player.hp for player in engine.state.players] == [4, 4]
    assert tuple(engine.state.player(0).hand) == (HEARTS[0], CLUBS[0], CLUBS[1], CLUBS[2])
    assert FA in engine.state.discard


def test_fire_attack_discard_step_offers_only_same_suit_cards_from_legal_actions():
    """候选面 = 同花色手牌 + 一条 Pass；别的花色的牌不在候选里（成对反例）。"""
    engine = _two_seat_engine(
        user_hand=(FA, SPADES[1], HEARTS[0], CLUBS[0]),
        target_hand=(SPADES[0],),
        top=(CLUBS[1], CLUBS[2]),
        spy=True,
    )
    user, _target = _play_fire_attack(
        engine, _Scenario(target=1, reveal=SPADES[0], discard=SPADES[1])
    )

    (query,) = _select_queries(user)
    offered = {
        query.slots[index].card.card_id
        for action in query.legal_actions
        if isinstance(action, SelectCards)
        for index in action.picks
    }
    assert offered == {SPADES[1]}, "只给同花色的那一张"
    assert not ({HEARTS[0], CLUBS[0]} & offered)
    assert len(engine.damage_frames()) == 1
    assert [player.hp for player in engine.state.players] == [4, 3]


def test_fire_attack_damage_runs_through_the_real_damage_modifiers():
    """火焰伤害进入统一管线：目标穿**藤甲** ⇒ 1 + 1 = 2（spec §5.1.4 末条）。"""
    vine = cards_of_type(PACK, "vine_armor")[0]
    engine = _two_seat_engine(
        user_hand=(FA, SPADES[1]),
        target_hand=(SPADES[0],),
        target_equip=(vine,),
        top=(CLUBS[0], CLUBS[1]),
        spy=True,
    )
    _play_fire_attack(engine, _Scenario(target=1, reveal=SPADES[0], discard=SPADES[1]))

    (damage,) = engine.damage_frames()
    assert (damage["amount"], damage["nature"]) == (1, DamageNature.FIRE)
    assert _hp_changes(engine) == [(1, 4, 2)], "火攻 1 + 藤甲 +1（由伤害修正链落地）"
