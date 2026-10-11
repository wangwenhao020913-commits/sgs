"""B 族：伤害后触发、逐点触发与【奸雄】/【反馈】/【遗计】（票 05）。

落点与票 05 §验收一致，全部走**外部缝**：

- **记录流**：``DAMAGE_AFTER_TAKE`` 的时机事件序列（逐点触发在记录流里就是同一帧里
  **多条** ``DAMAGE_AFTER_TAKE``，故"触发了几轮"读它最直接）；
- **询问缝**：``SKILL_ACTIVATION`` 的**次数**（【遗计】2 次、【奸雄】1 次）、
  ``SELECT_CARDS`` 的选择面（``query.slots`` 的隐藏槽 / 可见槽、``revealed`` 授予）；
- **终局状态**：手牌 / 装备 / 处理区 / 牌堆（"拿到的是哪张牌""牌去哪了"）。

**时点精确的局面这样造**：人为局面 + 直接压一个 ``FrameKind.DAMAGE`` 子帧（与引擎在
【杀】命中时压的形状逐字相同：``amount`` / ``target`` / ``source`` / ``card`` /
``channel``），再推进入栈。这使"受到 2 点伤害"是一个精确构造的事实，不必先跑一遍
【杀】与【闪】的窗口。

**判据怎么读**：''伤害被减为 0''读**帧标签**（:data:`~sgs.resolution.Tag.PREVENTED`，
【寒冰剑】类效果打的）；''逐点''读 ``DAMAGE_AFTER_TAKE`` 记录条数与技能询问条数；
''没泄露''读**别人**座位询问里的 ``view`` 与记录流里有没有带牌面的移动。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.resolution import Tag, add_tag
from sgs.state import (
    FrameKind,
    Pass,
    SelectCards,
    SelectCardsQuery,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_108")

JIANXIONG = "hero.caocao.jianxiong"
FANKUI = "hero.simayi.fankui"
YIJI = "hero.guojia.yiji"

DAMAGE_AFTER_TAKE = Timing.DAMAGE_AFTER_TAKE


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _advance(engine, players, stop) -> Engine:
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


def _pass_or_first(query):
    """最小合法应答：响应 / 濒死窗口放弃，其余取候选里第一个。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    return query.legal_actions[0]


class Scripted:
    """技能发动一律同意；``select``（``(query, round) -> Action``）应答选牌询问。"""

    def __init__(self, select=None, *, activate: bool = True) -> None:
        self._select = select
        self.activate = activate
        self.activation_queries: list = []
        self.selections: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION":
            self.activation_queries.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if query.kind == "SELECT_CARDS" and self._select is not None:
            self.selections.append(query)
            return self._select(query, len(self.selections) - 1)
        return _pass_or_first(query)


def _damage_frame(state, card: int | None, *, amount: int, source: int | None):
    """与引擎同形的 ``DAMAGE`` 子帧载荷。"""
    channel = state.card(card) if card is not None else None
    return {
        "source": source,
        "targets": (1,),
        "card": channel,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": 1,
        "channel": channel,
        "nature": "NORMAL",
        "chain": False,
        "amount": amount,
    }


def _damage_engine(
    *,
    target: SeatSpec | None = None,
    seats_after: int = 0,
    amount: int = 1,
    card: int | None = None,
    processing: tuple[int, ...] | None = None,
    source: int | None = 0,
    deck: tuple[int, ...] | None = None,
    prevented: bool = False,
    seed: int = 7,
):
    """压一个 ``DAMAGE`` 子帧并推进到它出栈；返回 ``(engine, players)``。

    座位 0 是伤害来源，座位 1 是被伤害者（``target`` 覆盖它的 ``SeatSpec``）。
    """
    if card is None:
        card = _cards("slash")[0]
    if processing is None:
        processing = (card,)
    specs = (
        [SeatSpec()]
        + [target if target is not None else SeatSpec()]
        + [SeatSpec() for _ in range(seats_after)]
    )
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=(
            deck
            if deck is not None
            else _deck_excluding(card, *processing)
        ),
        processing=processing,
    )
    engine = Engine.from_state(state, seed=seed)
    players = [Scripted() for _ in range(len(specs))]
    frame = engine.push_frame(
        FrameKind.DAMAGE, _damage_frame(state, card, amount=amount, source=source),
        timing=Timing.DAMAGE_DEAL,
    )
    if prevented:
        add_tag(frame, Tag.PREVENTED)
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )
    return engine, players


def _timings(engine, timing: Timing) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is timing
    ]


def _activation_count(engine, skill_id: str) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and record.query.skill_id == skill_id
    )


def _zones_of(state, card_id: int) -> list[tuple[str, int | None]]:
    """``card_id`` 现在落在哪些区（六区坐标）。"""
    found: list[tuple[str, int | None]] = []
    if card_id in state.deck:
        found.append(("deck", None))
    if card_id in state.processing:
        found.append(("processing", None))
    if card_id in state.discard:
        found.append(("discard", None))
    for player in state.players:
        if card_id in player.hand:
            found.append(("hand", player.seat))
        if card_id in player.equip:
            found.append(("equip", player.seat))
        if card_id in player.judge:
            found.append(("judge", player.seat))
    return found


def _total_cards(state) -> int:
    return (
        len(state.deck)
        + len(state.discard)
        + len(state.processing)
        + sum(len(p.hand) + len(p.equip) + len(p.judge) for p in state.players)
    )


def _pick_slot(query: SelectCardsQuery, predicate) -> SelectCards:
    """选 ``query.slots`` 里第一个满足 ``predicate`` 的槽位（隐藏槽 / 可见槽各一用）。"""
    index = next(i for i, slot in enumerate(query.slots) if predicate(slot))
    return next(
        action
        for action in query.legal_actions
        if isinstance(action, SelectCards) and action.picks == (index,)
    )


# ---------------------------------------------------------------------------
# ① 逐点触发（票 05 §验收 1）
# ---------------------------------------------------------------------------


def test_two_points_of_damage_ask_yiji_twice_and_jianxiong_once():
    """受到 2 点伤害 ⇒【遗计】被问 2 次（逐点）；【奸雄】只在第一轮有机会。"""
    card = _cards("slash")[0]
    engine, _players = _damage_engine(target=SeatSpec(hero="guojia"), amount=2, card=card)

    assert len(_timings(engine, DAMAGE_AFTER_TAKE)) == 1, "一条时机记录覆盖逐点派发"
    assert _activation_count(engine, YIJI) == 2, "【遗计】逐点 ⇒ 问 2 次"

    card = _cards("slash")[1]
    engine, _players = _damage_engine(target=SeatSpec(hero="caocao"), amount=2, card=card)

    assert len(_timings(engine, DAMAGE_AFTER_TAKE)) == 1
    assert _activation_count(engine, JIANXIONG) == 1, "非逐点 ⇒ 只在第一轮问一次"


def test_one_point_of_damage_asks_both_once():
    """受到 1 点伤害 ⇒ 两者各 1 次（``06`` §1 的关键验证）。"""
    engine, _players = _damage_engine(target=SeatSpec(hero="guojia"), amount=1)
    assert _activation_count(engine, YIJI) == 1

    engine, _players = _damage_engine(target=SeatSpec(hero="caocao"), amount=1)
    assert _activation_count(engine, JIANXIONG) == 1


# ---------------------------------------------------------------------------
# ② 伤害减为 0 ⇒ B 族一次都不触发（票 05 §验收 2）
# ---------------------------------------------------------------------------


def test_a_prevented_damage_never_triggers_the_family():
    """【寒冰剑】类"防止此伤害"⇒ 帧标签 ``PREVENTED`` ⇒ B 族一次都不问、牌不搬。

    时机记录本身照落一条（M2 的【寒冰剑】路径就是这样，改它会动既有基准），被挡掉的是
    **派发**——这正是"伤害为 0 即防止"的既有语义不被"受到伤害后"的时点绕开。
    """
    slash = _cards("slash")[0]
    engine, _players = _damage_engine(
        target=SeatSpec(hero="caocao"), card=slash, prevented=True
    )

    assert _activation_count(engine, JIANXIONG) == 0, "B 族一次都不问"
    assert engine.state.processing == [slash], "防止伤害 ⇒【奸雄】不搬牌"
    assert engine.state.player(1).hand == []


# ---------------------------------------------------------------------------
# ③ 【奸雄】拿到的正是造成伤害的那张牌（票 05 §验收 3）
# ---------------------------------------------------------------------------


def test_jianxiong_takes_the_card_that_dealt_the_damage():
    slash = _cards("slash")[0]
    engine, _players = _damage_engine(target=SeatSpec(hero="caocao"), card=slash)

    assert _activation_count(engine, JIANXIONG) == 1
    assert slash in engine.state.player(1).hand, "牌从处理区进了曹操的手牌"
    assert engine.state.processing == []
    assert slash not in engine.state.discard, "不是被弃置，而是被获得"
    assert _zones_of(engine.state, slash) == [("hand", 1)]


def test_jianxiong_is_skipped_when_the_card_is_no_longer_in_the_processing_zone():
    """牌已被移走（【寒冰剑】类效果 / 已被别人获得）⇒ 跳过且不报错。"""
    slash = _cards("slash")[0]
    other = _cards("peach")[0]
    engine, _players = _damage_engine(
        target=SeatSpec(hero="caocao"), card=slash, processing=(other,)
    )

    assert engine.state.player(1).hand == []
    assert _activation_count(engine, JIANXIONG) == 0, "找不到那张牌 ⇒ 连问都不问"
    assert engine.state.processing == [other]


# ---------------------------------------------------------------------------
# ④ 【反馈】的选取面：来源为空不进候选；手牌隐藏槽 + 装备可见槽（票 05 §验收 4）
# ---------------------------------------------------------------------------


def test_fankui_is_not_offered_when_the_damage_has_no_source():
    engine, _players = _damage_engine(
        target=SeatSpec(hero="simayi"), card=_cards("slash")[0], source=None
    )

    assert _activation_count(engine, FANKUI) == 0


def test_fankui_is_not_offered_when_the_source_has_no_cards():
    engine, _players = _damage_engine(target=SeatSpec(hero="simayi"), source=0)

    assert _activation_count(engine, FANKUI) == 0, "来源无牌可拿 ⇒ 不进候选"


def test_fankui_offers_the_sources_hand_hidden_and_equip_visible():
    """来源有手牌 + 装备 ⇒ 选取面覆盖两区；手牌槽 ``card is None``、装备槽可见。"""
    slash = _cards("slash")[0]
    source_hand = (_cards("peach")[0], _cards("peach")[1])
    source_equip = (_cards("crossbow")[0],)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=source_hand, equip=source_equip),
            SeatSpec(hero="simayi"),
        ],
        deck=_deck_excluding(slash, *source_hand, *source_equip),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )

    def select(query, _round):
        return _pick_slot(query, lambda slot: slot.card is None)

    players = [Scripted(), Scripted(select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert _activation_count(engine, FANKUI) == 1
    assert len(players[1].selections) == 1, "【反馈】只问一次选牌"
    query = players[1].selections[0]
    hidden = [slot for slot in query.slots if slot.card is None]
    visible = [slot for slot in query.slots if slot.card is not None]

    assert len(hidden) == 2, "来源的两张手牌是隐藏槽（只有位置）"
    assert all(slot.ref.zone is Zone.HAND for slot in hidden)
    assert len(visible) == 1, "来源的装备是可见槽"
    assert visible[0].card.card_id == source_equip[0]
    assert visible[0].ref == ZoneRef(Zone.EQUIP, 0)
    owner_hand = list(engine.state.player(1).hand)
    assert len(owner_hand) == 1, "选中隐藏槽并结算后才揭晓：那张手牌进了司马懿手里"
    assert owner_hand[0] in source_hand
    remaining = [card_id for card_id in source_hand if card_id != owner_hand[0]]
    assert sorted(engine.state.player(0).hand) == sorted(remaining)
    assert engine.state.player(0).equip == list(source_equip), "装备没被动过"


def test_fankui_can_take_a_visible_equipment_card():
    """选取面覆盖两区：也能拿走**装备区**那张可见牌。"""
    slash = _cards("slash")[0]
    source_hand = (_cards("peach")[0],)
    source_equip = (_cards("crossbow")[0],)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=source_hand, equip=source_equip),
            SeatSpec(hero="simayi"),
        ],
        deck=_deck_excluding(slash, *source_hand, *source_equip),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )

    def select(query, _round):
        return _pick_slot(query, lambda slot: slot.card is not None)

    players = [Scripted(), Scripted(select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert source_equip[0] in engine.state.player(1).hand
    assert engine.state.player(0).equip == []


# ---------------------------------------------------------------------------
# ⑤ 【遗计】看牌堆顶 2 张：不泄露、交给后牌守恒（票 05 §验收 5）
# ---------------------------------------------------------------------------


def _yiji_select(query, _round_index):
    """【遗计】的应答：每轮把面内**第一张**交给郭嘉自己（一轮一张）。"""
    return next(
        action
        for action in query.legal_actions
        if isinstance(action, SelectCards) and action.picks == (0,)
    )


def test_yiji_sees_the_top_two_cards_and_gives_them_away_without_leaking_them():
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    top = deck[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia")],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    players = [Scripted(), Scripted(_yiji_select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert _activation_count(engine, YIJI) == 1, "1 点伤害 ⇒ 只问一次"
    assert len(players[1].selections) == 1
    query = players[1].selections[0]

    assert tuple(card.card_id for card in query.revealed) == top, "授予牌堆顶 2 张（自顶向下）"
    assert query.view.deck_count == len(deck), "授予不改牌堆张数"
    assert all(slot.card is not None for slot in query.slots), "观看的牌面在面内可见"
    for record in engine.records:
        if record.kind == "query" and record.query is not query:
            assert record.query.revealed == (), "牌面只出现在被授予的那次询问里"
    # **观看本身不产 delta**：授予的牌面只在上面那次询问的 ``revealed`` 里；带牌面的
    # ``card_move`` 只有"交给"那一条（观看不搬牌）。
    moved = [
        tuple(card.card_id for card in record.cards)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and set(card.card_id for card in record.cards) & set(top)
    ]
    assert moved == [(top[0],)], "观看不搬牌；只有'交给'那一条移动"

    assert engine.state.player(1).hand == [top[0]], "选中那张进了郭嘉手里（结算后揭晓）"
    assert engine.state.deck == list(deck[1:]), "被观看的牌离开牌堆顶，其余顺序不变"
    assert _total_cards(engine.state) == len(PACK.deck.cards), "牌守恒仍成立"


def test_declining_yiji_leaves_the_deck_untouched():
    """``Pass`` = 这张不交给任何人：一个钩子都不搬牌、牌堆不动。"""
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia")],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )

    def decline(query, _round_index):
        return next(
            action for action in query.legal_actions if isinstance(action, Pass)
        )

    players = [Scripted(), Scripted(decline)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert engine.state.player(1).hand == []
    assert engine.state.deck == list(deck), "答 Pass ⇒ 牌堆一张不动"
    assert _total_cards(engine.state) == len(PACK.deck.cards)


def test_yiji_sees_a_fresh_top_two_on_each_damage_point():
    """逐点 ⇒ 每一轮各看一次牌堆顶（上一轮拿走的牌不再出现）。"""
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia")],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=2, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    players = [Scripted(), Scripted(_yiji_select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert len(players[1].selections) == 2, "2 点伤害 ⇒【遗计】问两轮"
    first, second = players[1].selections
    assert tuple(card.card_id for card in first.revealed) == deck[:2]
    assert tuple(card.card_id for card in second.revealed) == deck[1:3], "第二轮看新的牌堆顶"
    assert engine.state.player(1).hand == [deck[0], deck[1]]
    assert _total_cards(engine.state) == len(PACK.deck.cards)


# ---------------------------------------------------------------------------
# ⑥ 濒死优先于"受到伤害后"（票 05 §验收 6）
# ---------------------------------------------------------------------------


def test_near_death_timings_precede_damage_after_take_in_the_record_stream():
    """一次打到 0 体力的伤害：``NEAR_DEATH_*`` 三个时机都排在 ``DAMAGE_AFTER_TAKE`` 之前。

    濒死窗口里由**受伤者自己出【桃】**救回 1 点体力——这样伤害链才走得到"受到伤害后"，
    否则整条链在终局清栈时被丢弃（死亡路径，见 ``test_death.py``），时序无从观察。
    """
    slash = _cards("slash")[0]
    peach = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(),
            SeatSpec(hp=1, hp_max=4, hero="caocao", hand=(peach,)),
        ],
        deck=_deck_excluding(slash, peach),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )

    def select(query, _round):
        del query
        raise AssertionError("本局不该有选牌询问")

    class Rescuer(Scripted):
        def respond(self, query):
            if query.kind == "PEACH":
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "UseCard":
                        return action
            return super().respond(query)

    players = [Scripted(), Rescuer(select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert engine.state.player(1).hp == 1, "被救回 1 点，伤害链走完"
    order = [
        record.timing
        for record in engine.records
        if record.kind == "timing"
        and record.timing
        in (
            Timing.NEAR_DEATH_ENTER,
            Timing.NEAR_DEATH_AT,
            Timing.NEAR_DEATH_AFTER,
            DAMAGE_AFTER_TAKE,
        )
    ]

    assert order[-1] is DAMAGE_AFTER_TAKE, "受到伤害后排在濒死之后"
    assert order[:3] == [
        Timing.NEAR_DEATH_ENTER,
        Timing.NEAR_DEATH_AT,
        Timing.NEAR_DEATH_AFTER,
    ]
    assert _activation_count(engine, JIANXIONG) == 1
