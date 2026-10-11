"""D 族：失去牌后（票 07）。

落点与票 07 §验收一致，全部走**外部缝**：

- ``Engine.from_state`` 的人为局面 + 脚本玩家驱动的**记录流**（``MOVE_AFTER`` 时机事件与
  紧随其后的摸牌）；
- 终局状态（手牌数、装备区、弃牌堆）。

两条武将技能的逐字语义（票 07 §要做什么 6）：

- 陆逊【连营】：``from_zone == HAND ∧ to_zone != HAND ∧`` 移动后 ``hand`` 为空 ⇒ 摸 1 张；
- 孙尚香【枭姬】：``from_zone == EQUIP``（**含顶替**：先旧后新的两条移动里，旧装备那条
  算失去）⇒ 摸 2 张。

"失去"与 :class:`~sgs.state.MoveReason` **无关**（使用 / 弃置 / 交给 / 被获得 / 重铸 /
顶替全算），**摸牌不算**（票 07 §要做什么 5 / R5-Q3）；无提供者时**零派发**
（票 07 §验收 3，记录流逐字节不变由 ``tests/test_replays.py`` 的 34 条基准钉住）。

**时点精确的局面这样造**：先跑到"自己的出牌阶段开始"（摸牌阶段已过完），再把手牌种进
座位（:func:`_seed_hand`）——与 ``support.build_state`` 的人为局面同一姿态，只是把种牌
推迟到回合跑起来之后。这样"掉最后一张手牌"才是真的只有那一张。

**判据怎么读**：技能都是"移动后"的时点性质，故断言读**记录流**——某条移动之后
"牌堆 → 该座手牌"的摸牌事件（:func:`_draws_to`）才是技能真的摸了牌的证据；"停在某一微步
再数手牌"会随停在哪个微步漂移，不作为主判据。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    EndPhase,
    MoveReason,
    Pass,
    QueryKind,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_108")

LIANYING = "hero.luxun.lianying"
XIAOJI = "hero.sunshangxiang.xiaoji"

MOVE_AFTER = Timing.MOVE_AFTER


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _activate(query):
    """技能发动询问：把候选里的 ``ActivateSkill`` 交回去（"你可以"由玩家决定）。"""
    if query.kind != "SKILL_ACTIVATION":
        return None
    for action in query.legal_actions:
        if getattr(action, "kind", None) == "ActivateSkill":
            return action
    return None


def _pass_or_end(query):
    """最小合法应答：响应窗口放弃、出牌阶段结束阶段、弃牌阶段取第一个候选。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    if query.kind == "PLAY_PHASE":
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    return query.legal_actions[0]


class Scripted:
    """先跑 ``handler``；返回 ``None`` 时先试技能发动、再退到 :func:`_pass_or_end`。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        action = _activate(query)
        if action is not None:
            return action
        return _pass_or_end(query)


def _advance(engine, players, stop) -> Engine:
    """逐步推进到 ``stop(engine)`` 成立（同 ``tests/test_near_death.py`` 的手写推进）。"""
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


def _at_play_phase(engine, players) -> Engine:
    """跑到**出牌阶段开始**（摸牌阶段已过完）：此刻种下手牌才是最精确的时点。"""
    return _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
            for record in e.records
        ),
    )


def _seed_hand(engine, seat: int, cards: tuple[int, ...]) -> None:
    """把测试要用的手牌**换成** ``cards``（构造局面的入口，不新增领域机制）。

    先丢掉摸牌阶段发进来的牌：时点精确的局面（"手里就这一张"）必须这样起步，否则
    "掉最后一张"根本不成立。
    """
    hand = engine.state.player(seat).hand
    hand.clear()
    hand.extend(cards)


def _own_play_phase(state, cards: tuple[int, ...], *, seat: int = 0, seed: int = 7):
    """建局并跑到 ``seat`` 的出牌阶段开始，种下 ``cards``；返回 ``(engine, players)``。"""
    players = [Scripted(), Scripted()]
    engine = _at_play_phase(Engine.from_state(state, seed=seed), players)
    _seed_hand(engine, seat, cards)
    return engine, players


# ---------------------------------------------------------------------------
# 记录流读取
# ---------------------------------------------------------------------------


def _after(engine, card_id: int) -> list:
    """``card_id`` 那条 ``MOVE_AFTER`` 记录之后的所有记录（"那次时点之后发生了什么"）。"""
    for position, record in enumerate(engine.records):
        if record.kind != "timing" or record.timing is not MOVE_AFTER:
            continue
        if [card.card_id for card in record.cards] == [card_id]:
            return list(engine.records[position + 1 :])
    return []


def _drawn_to(records, seat: int) -> list[int]:
    """这批记录里"牌堆 → ``seat`` 手牌"的摸牌（``DRAW``）带的牌 id。"""
    return [
        card.card_id
        for record in records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DRAW"
        and record.to_zone.zone.value == "hand"
        and record.to_zone.seat == seat
        for card in record.cards
    ]


def _move_after_cards(engine) -> list[int]:
    """记录流里全部 ``MOVE_AFTER`` 记录带的牌 id（顺序 = 派发顺序）。"""
    return [
        card.card_id
        for record in engine.records
        if record.kind == "timing" and record.timing is MOVE_AFTER
        for card in record.cards
    ]


def _timings(engine, timing: Timing) -> list:
    """记录流里 ``timing`` 的 ``(frame_id, depth)`` 序列（票 07 §验收 4 的 depth 判据）。"""
    return [
        (record.frame_id, record.depth)
        for record in engine.records
        if record.kind == "timing" and record.timing is timing
    ]


def _move_after_records(engine) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is MOVE_AFTER
    ]


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


def _lost(engine, card_id: int, from_zone: str = "hand") -> bool:
    """``card_id`` 已经离开 ``from_zone``（那条 ``card_move`` 已记下）。"""
    return any(entry[0] == from_zone for entry in _moves_of(engine, card_id))


def _settled(engine, card_id: int, from_zone: str = "hand") -> bool:
    """停止条件：这次失去**已经结算完**（``card_id`` 离开原区、且结算栈已空）。

    "栈空"是"钩子里的摸牌也已经落地"的可判决据：插入结算（技能摸牌 / 判定）都压栈，
    栈回归空说明这一整段跑完了。停在"刚记下 card_move"会看不到钩子后来摸的那张牌。
    """
    return _lost(engine, card_id, from_zone) and not engine.stack and engine.pending is None


def _hand(engine, seat: int) -> tuple[int, ...]:
    return tuple(engine.state.player(seat).hand)


def _play_phase_player(card_id: int, *, seat: int = 0):
    """出牌阶段只用 ``card_id``（座位匹配时）；没有它就**结束出牌阶段**。

    刻意不退回"取候选里的第一张"：那些候选是随机摸到的牌，会在被测链跑起来之前把
    局面改道（把要用的那张牌弃掉、或替被测角色打出别的牌）。
    """

    def handler(query):
        if query.kind == "PLAY_PHASE" and query.actor == seat:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == card_id:
                    return action
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
        return None

    return handler


# ---------------------------------------------------------------------------
# 陆逊【连营】：掉最后一张手牌 ⇒ 摸 1 张；掉到还剩 ≥1 张 ⇒ 不触发
# ---------------------------------------------------------------------------


def test_lianying_draws_when_the_hand_empties_on_use():
    """使用掉最后一张手牌（手牌 → 处理区）⇒ 恰一次"失去牌后"、摸 1 张（票 07 §验收 1）。"""
    peach = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=3, hero="luxun"), SeatSpec()],
        deck=_deck_excluding(peach),
        turn_seat=0,
    )
    engine, _ = _own_play_phase(state, (peach,))
    players = [Scripted(_play_phase_player(peach)), Scripted()]

    _advance(engine, players, lambda e: _settled(e, peach) and len(_hand(e, 0)) == 1)

    assert _moves_of(engine, peach)[0] == ("hand", "processing", "USE"), "先失去那张牌"
    assert _drawn_to(_after(engine, peach), 0) != [], "连营真的摸了牌"
    assert len(_hand(engine, 0)) == 1, "掉光后手牌恰一张（林牌由 :func:`_own_play_phase` 种下）"


def test_lianying_does_not_fire_while_cards_remain_in_hand():
    """掉到还剩 ≥1 张 ⇒ 不触发（票 07 §验收 1 的反面对照，断言非空转）。"""
    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=3, hero="luxun"), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )
    engine, _ = _own_play_phase(state, (first, second))
    players = [Scripted(_play_phase_player(first)), Scripted()]

    _advance(engine, players, lambda e: _settled(e, first))

    assert _moves_of(engine, first)[0] == ("hand", "processing", "USE")
    assert _drawn_to(_after(engine, first), 0) == [], "手牌没空 ⇒ 连营没摸牌"
    assert _hand(engine, 0) == (second,), "第二张还在手里"


def test_lianying_ignores_the_draw_phase_move():
    """摸牌（牌堆 → 手牌）不算失去（票 07 §验收 1 的"摸牌把手牌从 0 变 1 不触发"）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="luxun"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )
    players = [Scripted(), Scripted()]
    engine = _at_play_phase(Engine.from_state(state, seed=7), players)

    assert len(_hand(engine, 0)) == 2, "摸牌阶段把手牌从 0 变 2（没有种牌，保留原样）"
    assert _move_after_cards(engine) == [], "摸牌不派发'失去牌后'"


def test_lianying_fires_when_a_discard_empties_the_hand():
    """弃置掉最后一张手牌（手牌 → 弃牌堆）⇒ 摸 1 张（票 07 §验收 1 的"弃置"）。

    座位 0 是陆逊（【连营】的载体），这次弃置直接经移动入口发起——与【制衡】/【贯石斧】
    的弃置走的是同一个 ``move_in_frame``；断言的是"失去"这条判据本身。
    """
    peach = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="luxun"), SeatSpec()],
        deck=_deck_excluding(peach),
        turn_seat=0,
    )
    engine, _ = _own_play_phase(state, (peach,))
    engine.move_in_frame(
        None,
        engine.state.refs((peach,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=0,
        actor=0,
    )
    # 这次移动的钩子会问"是否发动【连营】"；引擎在帧外发出询问，故用驱动循环应答它。
    _advance(engine, [Scripted(), Scripted()], lambda e: _drawn_to(_after(e, peach), 0) != [])

    assert ("hand", "discard", "DISCARD") in _moves_of(engine, peach), "弃置走同一个移动入口"
    assert peach in _move_after_cards(engine), "弃置算失去牌后"
    assert _drawn_to(_after(engine, peach), 0) != [], "弃光 ⇒ 连营摸牌"
    assert len(_hand(engine, 0)) == 1, "弃光后手牌恰一张"


def test_lianying_fires_when_the_discard_phase_empties_the_hand():
    """**弃牌阶段**的弃置也走同一个移动入口（票 07 §验收 1 的"弃置"，`frame=None` 那条路）。

    体力 1、手牌 2 张 ⇒ 弃牌阶段一次弃 1 张；断言弃置那条移动确实落进了记录流，
    且这一次移动派发了 ``MOVE_AFTER`` 时机（是否有提供者都按"每次移动一条"落）。
    """
    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=1, hero="luxun"), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )
    engine, _ = _own_play_phase(state, (first, second))

    def discard_all(query):
        if query.kind == "DISCARD":
            return query.legal_actions[0]
        return None

    _advance(
        engine,
        [Scripted(discard_all), Scripted()],
        lambda e: _lost(e, first) and first in _move_after_cards(e),
    )

    assert ("hand", "discard", "DISCARD") in _moves_of(engine, first), "弃置走同一个移动入口"


def test_lianying_fires_when_the_last_hand_card_is_given_to_another_character():
    """交给（手牌 → 别人手牌）也触发【连营】（票 07 §验收 1 的"交给"一侧）。

    座位 0 用制衡把唯一的手牌弃掉之后，那批牌**再从处理区进了目标的手牌**——
    这条路径上"归属变成目标"的那次移动同样算失去（与"使用 / 弃置"并列的来源）。
    本用例只验"手牌 → 别人手牌"这条移动形状：由座位 0 直接发起一次交给。
    """
    filler = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="luxun"), SeatSpec()],
        deck=_deck_excluding(filler),
        turn_seat=0,
    )
    engine, _ = _own_play_phase(state, (filler,))
    # 直接经移动入口发起"交给"（交给的语义载体在票 04 的【仁德】里；本票只验失去判据）。
    engine.move_in_frame(
        None,
        engine.state.refs((filler,)),
        to_zone=ZoneRef(Zone.HAND, 1),
        reason=MoveReason.PUT,
        owner=1,
        actor=0,
    )
    engine._flush_pending_move()

    assert ("hand", "hand", "PUT") in _moves_of(engine, filler), "交给 = 手牌 → 目标手牌"
    assert filler in engine.state.player(1).hand, "目标拿到了那张牌"
    assert filler in _move_after_cards(engine), "交给也算失去牌后"


def test_lianying_fires_when_the_borrowed_weapon_is_handed_over():
    """交给（装备区 → 别人手牌）也算失去：与移动原因无关（票 07 §要做什么 5）。

    座位 0 用【借刀杀人】逼持刀的陆逊交出武器：武器从陆逊的**装备区**进座位 0 的手牌区，
    这次移动的 ``reason = LOSE`` 与"使用 / 弃置"都不同，判定只看区域。
    """
    borrowed, weapon, filler = (
        _cards("borrowed_sword")[0],
        _cards("crossbow")[0],
        _cards("peach")[0],
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(borrowed, filler)), SeatSpec(equip=(weapon,), hero="luxun")],
        deck=_deck_excluding(borrowed, weapon, filler),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)

    _advance(
        engine,
        [Scripted(_play_phase_player(borrowed, seat=0)), Scripted()],
        lambda e: _settled(e, weapon, from_zone="equip") and weapon in _hand(e, 0),
    )

    assert ("equip", "hand", "LOSE") in _moves_of(engine, weapon), "武器被交出"
    assert weapon in _hand(engine, 0), "武器进了使用者的手牌区"
    assert weapon in _move_after_cards(engine), "交给也算失去"


def test_lianying_does_not_fire_when_another_seat_empties_its_hand():
    """【连营】写的是"**你**失去最后的手牌"：别人掉光手牌不关持有者的事（票 14 §2）。

    反例的形状：座位 0 是陆逊且**手里有牌**，座位 1 掉光最后一张手牌。触发判据必须
    落在"失去牌的那个座位 == 技能持有者"，否则这条询问会替陆逊摸牌（票 13 实测的
    种子 18 崩溃正是这条越界触发把外层应答吃掉）。
    """
    filler, other = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="luxun", hand=(filler,)), SeatSpec(hand=(other,))],
        deck=_deck_excluding(filler, other),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    engine.move_in_frame(
        None,
        engine.state.refs((other,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=1,
        actor=1,
    )
    # 挂起的"失去牌后"派发也要跑完（`_settled` 只看栈与询问，看不到 `_move_pending`）。
    _advance(engine, [Scripted(), Scripted()], lambda e: _settled(e, other) and e._move_pending is None)

    assert ("hand", "discard", "DISCARD") in _moves_of(engine, other), "座位 1 掉光手牌"
    assert other in _move_after_cards(engine), "失去牌后照记"
    assert _drawn_to(_after(engine, other), 0) == [], "不是陆逊失去的牌 ⇒ 连营不摸"
    assert _hand(engine, 0) == (filler,), "陆逊的手牌一张没动"


# ---------------------------------------------------------------------------
# 统一约定（票 14 §1）：内联 `MOVE_AFTER` 挂起后，调用方**必须让出控制权**
# ---------------------------------------------------------------------------


def test_an_equipment_hook_that_suspends_on_move_after_yields_control():
    """装备技逐张弃目标装备、目标又是孙尚香时，必须**让出控制权**（票 14 §1）。

    寒冰剑的两次弃置都是 ``MOVE_AFTER`` 的来源；目标每一次掉装备都会问"是否发动
    【枭姬】"。``_ice_sword`` 若忽略 ``"stay"``，就会在第一条应答被消费之前继续问
    第二张，把两次询问的应答错位（票 14 已知缺陷族的同一形状）。

    判据：两张装备各进弃牌堆一次；两次"失去装备"各触发一次【枭姬】（共摸 4 张）。
    """
    horse, horse2, slash, ice = (
        _cards("chitu")[0],
        _cards("dawan")[0],
        _cards("slash")[0],
        _cards("ice_sword")[0],
    )
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), equip=(ice,)),
            SeatSpec(hero="sunshangxiang", equip=(horse, horse2)),
        ],
        deck=_deck_excluding(horse, horse2, slash, ice),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == slash:
                    return action
            return None
        if query.kind == "SKILL_ACTIVATION":
            for action in query.legal_actions:
                if getattr(action, "kind", None) == "ActivateSkill":
                    return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if query.kind == "SELECT_CARDS":
            for action in query.legal_actions:
                if getattr(action, "kind", None) == "SelectCards":
                    return action
        return None

    def defender(query):
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        return None

    engine = Engine.from_state(state, seed=7)
    players = [Scripted(attacker), Scripted(defender)]
    _advance(engine, players, lambda e: len(_drawn_to(e.records, 1)) >= 2)

    assert _moves_of(engine, horse).count(("equip", "discard", "DISCARD")) == 1, "同一张牌不搬第二遍"
    assert horse in engine.state.discard, "被寒冰剑弃掉的坐骑进了弃牌堆"
    after = _after(engine, horse)
    activations = [
        index
        for index, record in enumerate(after)
        if record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and record.query.actor == 1
    ]
    assert activations, "目标掉装备触发【枭姬】的『是否发动』询问"
    assert after[activations[0] + 1].kind == "action", (
        "让出控制权：询问之后紧接着就是它的应答——寒冰剑没有在应答被消费之前另发询问"
    )
    assert len(_drawn_to(after, 1)) >= 2, "枭姬真的摸了牌"


# ---------------------------------------------------------------------------
# 孙尚香【枭姬】：失去装备区里的一张牌 ⇒ 摸 2 张（含顶替）
# ---------------------------------------------------------------------------


def test_xiaoji_fires_when_equipment_is_taken_by_snatch():
    """被【顺手牵羊】拿走装备 ⇒ 摸 2 张（票 07 §验收 2）。"""
    snatch, weapon, filler = (
        _cards("snatch")[0],
        _cards("crossbow")[0],
        _cards("peach")[0],
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(snatch, filler)), SeatSpec(equip=(weapon,), hero="sunshangxiang")],
        deck=_deck_excluding(snatch, weapon, filler),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)

    _advance(
        engine,
        [Scripted(_play_phase_player(snatch, seat=0)), Scripted()],
        lambda e: _settled(e, weapon, from_zone="equip") and weapon in _hand(e, 0),
    )

    assert ("equip", "hand", "LOSE") in _moves_of(engine, weapon), "装备被拿走"
    assert not engine.state.player(1).equip, "装备区那张没了"
    assert len(_drawn_to(_after(engine, weapon), 1)) == 2, "枭姬摸 2 张"


def test_xiaoji_fires_on_replacement_even_though_the_equipment_is_not_discarded():
    """**顶替**（同槽先旧后新）触发【枭姬】：旧装备那条移动算失去（票 07 §验收 2 / R6-Q2）。

    座位 0 的装备区已有一匹进攻马，自己再使用同槽的另一匹进攻马 ⇒ 旧装备**先失去**、
    新装备再置入。旧装备那条移动的 ``reason`` 是 ``LOSE``（不是弃置），判定仍只看区域。
    """
    old_horse, new_horse = _cards("chitu")[0], _cards("dawan")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(new_horse,), equip=(old_horse,), hero="sunshangxiang"),
            SeatSpec(),
        ],
        deck=_deck_excluding(old_horse, new_horse),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)

    _advance(
        engine,
        [Scripted(_play_phase_player(new_horse, seat=0)), Scripted()],
        lambda e: old_horse in e.state.discard,
    )

    assert ("equip", "discard", "LOSE") in _moves_of(engine, old_horse), "顶替 = 旧装备失去"
    assert old_horse in engine.state.discard, "旧装备进了弃牌堆"
    assert new_horse in engine.state.player(0).equip, "新装备置入"
    _advance(
        engine,
        [Scripted(), Scripted()],
        lambda e: len(_drawn_to(_after(e, old_horse), 0)) == 2,
    )
    assert len(_drawn_to(_after(engine, old_horse), 0)) == 2, "枭姬摸 2 张"


def test_xiaoji_ignores_equipment_entering_the_equip_zone():
    """装备从手牌进入装备区**不**触发【枭姬】（票 07 §验收 2 的反面对照）。

    用手牌里的装备牌装备：两条移动是手牌 → 处理区 → 装备区，``from_zone`` 都不是装备区；
    摸牌阶段那两张更不是失去 ⇒ 一条 ``MOVE_AFTER`` 都不该有。
    """
    weapon = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(weapon,), hero="sunshangxiang"), SeatSpec()],
        deck=_deck_excluding(weapon),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)

    _advance(
        engine,
        [Scripted(_play_phase_player(weapon, seat=0)), Scripted()],
        lambda e: e.state.player(0).equip and weapon in e.state.player(0).equip,
    )

    assert weapon in engine.state.player(0).equip, "装备进了装备区"
    assert [entry[0] for entry in _moves_of(engine, weapon)] == ["hand", "processing"], (
        "两条移动的来源都不是装备区"
    )
    assert _drawn_to(_after(engine, weapon), 0) == [], "装备进场不是失去 ⇒ 枭姬没摸牌"


def test_xiaoji_does_not_fire_when_another_seat_loses_equipment():
    """【枭姬】写的是"**你**失去装备区里的一张牌"：别人掉装备不关持有者的事（票 14 §2）。

    与【连营】同一处越界：触发判据必须落在"失去牌的那个座位 == 技能持有者"。
    """
    filler, weapon = _cards("peach")[0], _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="sunshangxiang", hand=(filler,)), SeatSpec(equip=(weapon,))],
        deck=_deck_excluding(filler, weapon),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    engine.move_in_frame(
        None,
        engine.state.refs((weapon,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=1,
        actor=1,
    )
    _advance(engine, [Scripted(), Scripted()], lambda e: _settled(e, weapon, from_zone="equip") and e._move_pending is None)

    assert ("equip", "discard", "DISCARD") in _moves_of(engine, weapon), "座位 1 掉了装备"
    assert _drawn_to(_after(engine, weapon), 0) == [], "不是孙尚香失去的牌 ⇒ 枭姬不摸"
    assert _hand(engine, 0) == (filler,), "孙尚香的手牌一张没动"


# ---------------------------------------------------------------------------
# 帧外收尾移动也经同一个移动入口（code-review C1）
# ---------------------------------------------------------------------------


def _judge_card_left_for_processing(engine, card_id: int) -> bool:
    """延时锦囊那次帧外收尾移动已经落进记录流（判定区 → 处理区）。"""
    return ("judge", "processing", "LOSE") in _moves_of(engine, card_id)


def test_a_delayed_trick_leaving_the_judge_zone_dispatches_move_after():
    """判定阶段循环的帧外收尾移动也派发"失去牌后"（code-review C1）。

    局面上有 D 族提供者（座位 1 的陆逊）时，延时锦囊 JUDGE→PROCESSING 这次移动必须落
    一条 ``MOVE_AFTER`` 记录。票 07 把这三处帧外直调点登记为范围外缺口；C1 把
    ``Engine._move_cards`` 收口到 :meth:`Engine.move_in_frame` 后，这一处不再绕开移动入口。
    """
    indulgence = _cards("indulgence")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(judge=(indulgence,)), SeatSpec(hero="luxun")],
        deck=_deck_excluding(indulgence),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    _advance(
        engine,
        [Scripted(), Scripted()],
        lambda e: _judge_card_left_for_processing(e, indulgence),
    )

    assert indulgence in _move_after_cards(engine), (
        "判定区 → 处理区（帧外收尾）也经移动入口派发失去牌后"
    )


def test_the_judge_zone_move_stays_silent_without_a_move_after_provider():
    """无提供者 ⇒ 本次帧外移动零派发（票 07 §验收 3；M2 的 34 条基准逐字节不变）。"""
    indulgence = _cards("indulgence")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(judge=(indulgence,)), SeatSpec()],
        deck=_deck_excluding(indulgence),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    _advance(
        engine,
        [Scripted(), Scripted()],
        lambda e: _judge_card_left_for_processing(e, indulgence),
    )

    assert indulgence not in _move_after_cards(engine), "无提供者 ⇒ 一个时机事件都不发"

