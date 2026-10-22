"""M5-04：【酒】——出牌阶段加伤 / 濒死自救 / 每回合一次（spec D5 / §5.1 酒 / §4.3）。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流 + 一个只记「压栈帧载荷」
的 :class:`_FrameSpy`（``deal_bonus`` / ``use_timing`` 是帧私有事实，记录流里不落）。
军争牌只在 ``standard_ex_160`` 档里，故每个用例都显式选这一档（档位纪律）。

钉住的合同：

1. 出牌阶段只能对自己使用；对他人 / 空目标 = 不在候选集合内（提交即拒）；
2. 使用后 ``wine_active=True`` + 一条 ``wine_change``；本回合下一张【杀】的来源侧 +1，
   被【闪】抵消 / 未造成伤害也消耗，不留给再下一张；对锦囊 / 其它基本牌不生效、不消耗；
3. 濒死自救只能对自己、回复 1、**不产生**加伤；两种用法共用同一个每回合一次计数键；
4. 回合末清 ``wine_active``（含一条 ``wine_change=false``），下回合可再用；
5. 加伤只烘进**初始目标**的实际伤害：火杀 + 酒命中横置角色时，传导基数是 2，
   传导目标不再吃酒的 +1（spec §5.1.1 第 6 条）；
6. ``engine/`` 不认识「酒」这个牌名（只经 ``effects.is_slash`` 判【杀】家族）。
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.effects import CarrierKind, Modifier, ModifierPoint
from sgs.engine import Engine, IllegalActionError
from sgs.skills import catalog as catalog_module
from sgs.skills.catalog import SkillDef
from sgs.state import ActivateSkill, EndPhase, FrameKind, Pass, PlayCard, QueryKind, SelectCards, UseCard
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_ex_160")
WINE = cards_of_type(PACK, "wine")
SLASH = cards_of_type(PACK, "slash")
FIRE_SLASH = cards_of_type(PACK, "fire_slash")
THUNDER_SLASH = cards_of_type(PACK, "thunder_slash")
DODGE = cards_of_type(PACK, "dodge")
PEACH = cards_of_type(PACK, "peach")
DUEL = cards_of_type(PACK, "duel")
BARBARIAN = cards_of_type(PACK, "barbarian_invasion")
GREEN_DRAGON = cards_of_type(PACK, "green_dragon_blade")
EIGHT_DIAGRAMS = cards_of_type(PACK, "eight_diagrams")

ENGINE_ROOT = Path(__file__).resolve().parent.parent / "src" / "sgs" / "engine"


# ---------------------------------------------------------------------------
# 驱动：脚本参与者 + 逐步推进
# ---------------------------------------------------------------------------


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _minimal(query):
    """最小合法应答：出牌阶段结束、响应 / 濒死放弃、其余取第一个候选。"""
    if query.kind == QueryKind.PLAY_PHASE:
        return next(a for a in query.legal_actions if isinstance(a, EndPhase))
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _Scripted:
    """一个座位：先跑 ``handler``，返回 ``None`` 时退到最小应答。"""

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


def _advance(engine, players, stop, *, limit: int = 40_000) -> Engine:
    for _ in range(limit):
        if stop(engine) or engine.finished:
            return engine
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _at_play_phase(seat: int = 0):
    return lambda engine: (
        engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
        and engine.pending.actor == seat
    )


def _use(query, card_id: int, target: int):
    return _find(
        query,
        lambda action: isinstance(action, UseCard)
        and action.card.card_id == card_id
        and action.targets == (target,),
    )


def _wine_changes(engine) -> list[tuple[int, bool]]:
    return [
        (record.seat, record.active)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "wine_change"
    ]


def _use_frame_ids(engine) -> set[int]:
    return {
        record.frame_id
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind is FrameKind.USE
    }


def _hp_changes(engine) -> list[tuple[int, int, int]]:
    return [
        (record.seat, record.before, record.after)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "hp_change"
    ]


class _FrameSpy(Engine):
    """记下每一次压栈的帧类型 / frame_id / 载荷（帧私有事实只能这样观测）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pushed: list[tuple[FrameKind, int, dict[str, Any]]] = []

    def push_frame(self, kind, data, *, timing):  # type: ignore[no-untyped-def]
        frame = super().push_frame(kind, data, timing=timing)
        self.pushed.append((kind, frame.frame_id, dict(data)))
        return frame

    def payloads(self, kind: FrameKind) -> list[dict[str, Any]]:
        return [data for frame_kind, _fid, data in self.pushed if frame_kind is kind]


# ---------------------------------------------------------------------------
# 出牌阶段：只能对自己、置 wine_active + delta、每回合一次
# ---------------------------------------------------------------------------


def test_play_phase_wine_only_targets_itself_and_sets_wine_active_with_a_delta():
    wine = WINE[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(wine, WINE[1])), SeatSpec()],
        deck=_deck_excluding(wine, WINE[1]),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    _advance(engine, [_Scripted(), _Scripted()], _at_play_phase(0))
    query = engine.pending
    offers = [
        action
        for action in query.legal_actions
        if isinstance(action, UseCard) and action.card.card_id == wine
    ]
    assert [action.targets for action in offers] == [(0,)], "出牌阶段【酒】的合法目标只有自己"

    engine.submit(offers[0])
    _advance(engine, [_Scripted(), _Scripted()], _at_play_phase(0))

    assert engine.state.player(0).wine_active is True, "使用后加伤状态置位"
    assert _wine_changes(engine) == [(0, True)], "状态变化有一条 wine_change delta"
    resolve_frames = {
        record.frame_id
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.RESOLVE
    }
    record = next(
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "wine_change"
    )
    assert record.frame_id in resolve_frames, "置位落在【酒】自己的 RESOLVE 帧内"
    assert not [
        record
        for record in engine.records
        if record.kind == "frame" and record.frame_kind is FrameKind.DAMAGE
    ], "【酒】自身的效果不压伤害帧"


def test_play_phase_wine_rejects_other_and_empty_targets():
    wine = WINE[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(wine,)), SeatSpec()],
        deck=_deck_excluding(wine),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    _advance(engine, [_Scripted(), _Scripted()], _at_play_phase(0))
    query = engine.pending

    for targets in ((1,), ()):
        with pytest.raises(IllegalActionError):
            engine.submit(
                UseCard(card=engine.state.card(wine), targets=targets, answers_seq=query.seq)
            )

    assert engine.state.player(0).wine_active is False, "被拒的提交不碰状态"
    assert [
        record.accepted for record in engine.records if record.kind == "action"
    ] == [False, False], "两次拒绝都落在记录流里（拒绝本身可观测）"


def test_play_phase_wine_is_limited_to_once_per_turn():
    first, second = WINE[0], WINE[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second)), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    players = [_Scripted(), _Scripted()]

    _advance(engine, players, _at_play_phase(0))
    engine.submit(_use(engine.pending, first, 0))
    _advance(engine, players, _at_play_phase(0))

    query = engine.pending
    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, UseCard) and action.card.type == "wine"
    ], "同一回合的第二张【酒】不进候选"
    with pytest.raises(IllegalActionError):
        engine.submit(
            UseCard(card=engine.state.card(second), targets=(0,), answers_seq=query.seq)
        )
    assert engine.state.player(0).card_use_counts.get("wine") == 1, (
        "两种用法共用的计数键只记一次"
    )


# ---------------------------------------------------------------------------
# 下一张【杀】+1：来源侧链、三种【杀】、转化产物、不作用于其它牌、可叠加
# ---------------------------------------------------------------------------


def _wine_then_card(wine: int, card: int, *, target: int = 1, defender_hand: tuple[int, ...] = ()):
    """座位 0 先喝【酒】、再用 ``card`` 打座位 1；跑到这次使用链跑完即停。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(wine, card)), SeatSpec(hand=defender_hand)],
        deck=_deck_excluding(wine, card, *defender_hand),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    used: list[str] = []

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE and "card" not in used:
            if "wine" not in used:
                action = _use(query, wine, 0)
                if action is not None:
                    used.append("wine")
                    return action
            action = _use(query, card, target)
            if action is not None:
                used.append("card")
                return action
        return None

    players = [_Scripted(attacker), _Scripted()]
    _advance(
        engine,
        players,
        lambda e: "card" in used
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE,
    )
    return engine


def test_a_wine_boosted_slash_deals_one_extra_damage():
    """普通 / 火 / 雷【杀】都走同一条来源侧加伤：1 + 1 = 2。"""
    for type_id in ("slash", "fire_slash", "thunder_slash"):
        card = cards_of_type(PACK, type_id)[0]
        engine = _wine_then_card(WINE[0], card)

        assert engine.state.player(1).hp == 2, f"{type_id}：【酒】的 +1 只来自来源侧"
        assert engine.state.player(0).wine_active is False, "兑现给下一张【杀】后状态被消费"
        assert _wine_changes(engine) == [(0, True), (0, False)]
        consumed = [
            record
            for record in engine.records
            if record.kind == "delta" and record.delta_type == "wine_change" and not record.active
        ]
        assert consumed[0].frame_id in _use_frame_ids(engine), (
            "消费落在那张【杀】的 USE 帧"
        )


def test_the_wine_bonus_applies_to_a_converted_slash_product():
    """转化产品（龙胆：闪 → 杀）按**产物牌型**吃【酒】加伤。"""
    wine, dodge = WINE[0], DODGE[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(wine, dodge), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(wine, dodge),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    used: list[str] = []

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if "wine" not in used:
                action = _use(query, wine, 0)
                if action is not None:
                    used.append("wine")
                    return action
            if "slash" not in used:
                steps = (query.transform_preactivations or {}).get("hero.zhaoyun.longdan", ())
                if steps:
                    source = int(steps[0].pool[0].options[0])
                    targets = [
                        option for option in steps[1].pool[0].options if option == "seat:1"
                    ]
                    if targets:
                        used.append("slash")
                        return ActivateSkill(
                            skill_id="hero.zhaoyun.longdan",
                            picks=(source,),
                            choices=(targets[0],),
                            answers_seq=query.seq,
                        )
        return None

    players = [_Scripted(attacker), _Scripted()]
    _advance(
        engine,
        players,
        lambda e: "slash" in used
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE,
    )

    assert engine.state.player(1).hp == 2, "转化【杀】的伤害同样是 1 + 1"
    assert engine.state.player(0).card_use_counts.get("slash") == 1, "按产物牌名计入【杀】次数"


def test_the_wine_bonus_does_not_boost_tricks_and_is_not_consumed_by_them():
    """南蛮入侵造成 1（不是 2），且不消费加伤——下一张【杀】仍旧 +1。"""
    wine, barbarian, slash = WINE[0], BARBARIAN[0], SLASH[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(wine, barbarian, slash)), SeatSpec()],
        deck=_deck_excluding(wine, barbarian, slash),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    used: list[str] = []

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            for step, card, target in (
                ("wine", wine, 0),
                ("trick", barbarian, 1),
                ("slash", slash, 1),
            ):
                if step in used:
                    continue
                if step == "trick":
                    action = _find(
                        query,
                        lambda a: isinstance(a, UseCard)
                        and a.card.card_id == card
                        and a.targets == (1,),
                    )
                else:
                    action = _use(query, card, target)
                if action is not None:
                    used.append(step)
                    return action
        return None

    players = [_Scripted(attacker), _Scripted()]
    _advance(engine, players, lambda e: "trick" in used and e.state.player(1).hp == 3)
    assert engine.state.player(1).hp == 3, "锦囊伤害 = 1，【酒】不作用于锦囊"
    assert engine.state.player(0).wine_active is True, "使用锦囊不消费加伤"

    _advance(
        engine,
        players,
        lambda e: "slash" in used
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE,
    )
    assert engine.state.player(1).hp == 1, "加伤留给了下一张【杀】（1 + 1）"


def test_the_wine_bonus_is_not_consumed_by_other_basic_cards():
    """【桃】是另一张基本牌：不消费加伤，之后的【杀】仍 +1。"""
    wine, peach, slash = WINE[0], PEACH[0], SLASH[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=3, hand=(wine, peach, slash)),
            SeatSpec(),
        ],
        deck=_deck_excluding(wine, peach, slash),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    used: list[str] = []

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if "wine" not in used:
                action = _use(query, wine, 0)
                if action is not None:
                    used.append("wine")
                    return action
            if "peach" not in used:
                action = _use(query, peach, 0)
                if action is not None:
                    used.append("peach")
                    return action
            if "slash" not in used:
                action = _use(query, slash, 1)
                if action is not None:
                    used.append("slash")
                    return action
        return None

    players = [_Scripted(attacker), _Scripted()]
    _advance(engine, players, lambda e: "peach" in used and e.state.player(0).hp == 4)
    assert engine.state.player(0).hp == 4, "【桃】照常回复"
    assert engine.state.player(0).wine_active is True, "使用其它基本牌不消费加伤"

    _advance(
        engine,
        players,
        lambda e: "slash" in used
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE,
    )
    assert engine.state.player(1).hp == 2, "【杀】仍吃到 +1"


def test_using_equipment_before_the_slash_does_not_consume_the_wine_bonus():
    """装备牌也是"别的牌"：喝了【酒】先装八卦阵，加伤仍留给下一张【杀】。"""
    wine, armor, slash = WINE[0], EIGHT_DIAGRAMS[0], SLASH[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(wine, armor, slash)), SeatSpec()],
        deck=_deck_excluding(wine, armor, slash),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    used: list[str] = []

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            for step, card, target in (
                ("wine", wine, 0),
                ("equip", armor, 0),
                ("slash", slash, 1),
            ):
                if step in used:
                    continue
                action = _use(query, card, target)
                if action is not None:
                    used.append(step)
                    return action
        return None

    players = [_Scripted(attacker), _Scripted()]
    _advance(engine, players, lambda e: "equip" in used and e.state.player(0).equip)
    assert engine.state.player(0).wine_active is True, "使用装备不消费加伤"

    _advance(
        engine,
        players,
        lambda e: "slash" in used
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE,
    )
    assert engine.state.player(1).hp == 2, "【杀】仍吃到 +1"


def _install(monkeypatch, *definitions: SkillDef) -> None:
    """把构造的技能并进 ``catalog.CATALOG``（与 ``tests/test_hero_skills.py`` 同姿态）。"""
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, **{d.skill_id: d for d in definitions}}),
    )


def _damage_dealt_plus_one(monkeypatch) -> str:
    """构造的「造成伤害 +1」来源侧修正（古锭刀 / 其它加伤的代表）。"""

    def handler(state, ctx, current):
        del state
        return current + 1 if ctx.owner == ctx.subject else current

    fake = SkillDef(
        skill_id="test.wine.dealt_plus_one",
        locked=True,
        carrier=CarrierKind.EQUIPMENT,
        modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, handler),),
    )
    _install(monkeypatch, fake)
    return fake.skill_id


def test_the_wine_bonus_stacks_with_another_source_side_plus_one(monkeypatch):
    """酒 +1 与构造的另一条来源侧 +1 走同一条链：1 + 1 + 1 = 3（加法可交换）。"""
    fake = _damage_dealt_plus_one(monkeypatch)
    slash = SLASH[0]

    def run(*, wine: int | None) -> Engine:
        hand = (wine, slash) if wine is not None else (slash,)
        state = build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=hand, virtual_equipment=(fake,)),
                SeatSpec(),
            ],
            deck=_deck_excluding(*hand),
            turn_seat=0,
        )
        engine = Engine.from_state(state, seed=1)
        used: list[str] = []

        def attacker(query):
            if query.kind == QueryKind.PLAY_PHASE:
                if wine is not None and "wine" not in used:
                    action = _use(query, wine, 0)
                    if action is not None:
                        used.append("wine")
                        return action
                action = _use(query, slash, 1)
                if action is not None:
                    used.append("slash")
                    return action
            return None

        _advance(
            engine,
            [_Scripted(attacker), _Scripted()],
            lambda e: "slash" in used
            and not e.stack
            and e.pending is not None
            and e.pending.kind == QueryKind.PLAY_PHASE,
        )
        return engine

    assert run(wine=None).state.player(1).hp == 2, "构造的 +1 单独生效（1 + 1）"
    assert run(wine=WINE[0]).state.player(1).hp == 1, "两条来源侧 +1 叠加（1 + 1 + 1）"


# ---------------------------------------------------------------------------
# 消耗时点 = 使用：被闪也消耗；不留给再下一张；青龙追杀不能插【酒】
# ---------------------------------------------------------------------------


def test_a_dodged_slash_still_consumes_the_wine_bonus():
    wine, slash, dodge = WINE[0], SLASH[0], DODGE[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(wine, slash)), SeatSpec(hand=(dodge,))],
        deck=_deck_excluding(wine, slash, dodge),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    used: list[str] = []

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if "wine" not in used:
                action = _use(query, wine, 0)
                if action is not None:
                    used.append("wine")
                    return action
            if "slash" not in used:
                action = _use(query, slash, 1)
                if action is not None:
                    used.append("slash")
                    return action
        return None

    defender = _Scripted(
        lambda q: _find(q, lambda a: isinstance(a, UseCard) and a.card.type == "dodge")
    )
    _advance(
        engine,
        [_Scripted(attacker), defender],
        lambda e: "slash" in used
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE,
    )

    assert engine.state.player(1).hp == 4, "被【闪】抵消，没有造成伤害"
    assert engine.state.player(0).wine_active is False, "加伤在**使用**时就被消费"
    assert _wine_changes(engine) == [(0, True), (0, False)]


def test_a_green_dragon_continuation_is_not_boosted_and_cannot_drink_wine():
    """青龙续杀是"再下一张【杀】：不吃已经消费掉的加伤；追杀过程中没有出牌阶段窗口。"""
    wine, first, second, dodge = WINE[0], SLASH[0], SLASH[1], DODGE[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(wine, first, second), equip=(GREEN_DRAGON[0],)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(wine, first, second, GREEN_DRAGON[0], dodge),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    used: list[str] = []
    play_queries_at_seam = {"count": -1}

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if "wine" not in used:
                action = _use(query, wine, 0)
                if action is not None:
                    used.append("wine")
                    return action
            if "slash" not in used:
                action = _use(query, first, 1)
                if action is not None:
                    used.append("slash")
                    return action
            return None
        if query.kind == QueryKind.SKILL_ACTIVATION:
            # 青龙的接缝在【杀】的结算内部：此刻应恰好回过两个出牌阶段询问（酒 / 杀），
            # 中间没有新的出牌阶段窗口 ⇒ 【酒】不可能被插进追杀。
            play_queries_at_seam["count"] = len(
                [q for q in attacker_queries if q.kind == QueryKind.PLAY_PHASE]
            )
            return _find(query, lambda a: isinstance(a, ActivateSkill))
        if query.kind == QueryKind.SELECT_CARDS:
            for index, slot in enumerate(query.slots):
                if slot.card is not None and slot.card.card_id == second:
                    return _find(
                        query,
                        lambda a, i=index: isinstance(a, SelectCards) and a.picks == (i,),
                    )
        return None

    attacker_queries: list[Any] = []
    base = _Scripted(attacker)

    def record(query):
        attacker_queries.append(query)
        return base.handler(query)

    dodges = {"used": False}

    def defender(query):
        if query.kind == QueryKind.DODGE:
            if not dodges["used"]:
                dodges["used"] = True
                return _find(query, lambda a: isinstance(a, UseCard) and a.card.type == "dodge")
            return _find(query, lambda a: isinstance(a, Pass))
        return None

    _advance(
        engine,
        [_Scripted(record), _Scripted(defender)],
        lambda e: len(used) == 2
        and e.state.player(0).card_use_counts.get("slash") == 1
        and e.state.player(1).hp < 4
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE,
    )

    assert engine.state.player(1).hp == 3, "续杀只造成 1 点（加伤已被第一张【杀】消费）"
    assert engine.state.player(0).wine_active is False
    assert engine.state.player(0).card_use_counts.get("slash") == 1, "续杀不计次数"
    assert play_queries_at_seam["count"] == 2, (
        "青龙接缝期间没有新的出牌阶段询问 ⇒ 追杀过程中不能插入【酒】"
    )


# ---------------------------------------------------------------------------
# 连环：酒只烘进初始目标的实际伤害，传导目标不再吃来源侧 +1
# ---------------------------------------------------------------------------


def test_a_wine_boosted_fire_slash_conducts_with_the_boosted_base():
    wine, fire = WINE[0], FIRE_SLASH[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(wine, fire), identity=Identity.LORD),
            SeatSpec(chained=True, identity=Identity.REBEL),
            SeatSpec(chained=True, identity=Identity.REBEL),
            SeatSpec(chained=True, identity=Identity.REBEL),
        ],
        deck=_deck_excluding(wine, fire),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    engine = Engine.from_state(state, seed=1)
    used: list[str] = []

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if "wine" not in used:
                action = _use(query, wine, 0)
                if action is not None:
                    used.append("wine")
                    return action
            if "fire" not in used:
                action = _use(query, fire, 1)
                if action is not None:
                    used.append("fire")
                    return action
        return None

    _advance(
        engine,
        [_Scripted(attacker), _Scripted(), _Scripted(), _Scripted()],
        lambda e: "fire" in used
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and not e._conduction,
    )

    assert _hp_changes(engine) == [
        (1, 4, 2),  # 1 火伤 + 酒 +1 = 2，初始目标实际受到 2
        (2, 4, 2),  # 传导基数是 2；来源侧 +1 不再重算
        (3, 4, 2),
    ], "酒烘进初始目标的实际伤害，传导目标只再吃自己的受到伤害修正"


# ---------------------------------------------------------------------------
# 濒死自救：只能对自己、回复 1、不产生加伤；两种用法共用一个计数键
# ---------------------------------------------------------------------------


def _duel_into_near_death(*, hand: tuple[int, ...]):
    """座位 0 用【决斗】把自己打进濒死：1 血、无【杀】、座位 1 有一张【杀】。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=1, hand=hand), SeatSpec(hand=(SLASH[0],))],
        deck=_deck_excluding(*hand, SLASH[0]),
        turn_seat=0,
    )
    engine = _FrameSpy.from_state(state, seed=1)
    used: list[str] = []

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE and "duel" not in used:
            action = _use(query, DUEL[0], 1)
            if action is not None:
                used.append("duel")
                return action
        if query.kind == QueryKind.RESPOND:
            return _find(query, lambda a: isinstance(a, Pass))
        return None

    def defender(query):
        if query.kind == QueryKind.RESPOND:
            return _find(query, lambda a: isinstance(a, PlayCard) and a.card.type == "slash")
        return None

    return engine, [_Scripted(attacker), _Scripted(defender)]


def _near_death_offer_case(*, self_rescue: bool) -> None:
    """濒死者自己会用【酒】自救；别人（即使手里有【酒】）的求桃候选里没有它。"""
    healthy_wine = WINE[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(healthy_wine, SLASH[0])),
            SeatSpec(hp=1, hand=(WINE[1], PEACH[0])),
        ],
        deck=_deck_excluding(healthy_wine, SLASH[0], WINE[1], PEACH[0]),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            action = _use(query, SLASH[0], 1)
            if action is not None:
                return action
        return None

    player0 = _Scripted(attacker)
    player1 = _Scripted()
    players = [player0, player1]

    _advance(engine, players, lambda e: e.pending is not None and e.pending.kind == QueryKind.PEACH)
    first = engine.pending
    assert first.actor == 0, "求桃从当前回合角色开始"
    assert not [
        action
        for action in first.legal_actions
        if isinstance(action, UseCard) and action.card.type == "wine"
    ], "对他人的濒死不能用【酒】"
    with pytest.raises(IllegalActionError):
        engine.submit(
            UseCard(card=engine.state.card(healthy_wine), targets=(1,), answers_seq=first.seq)
        )

    expected = WINE[1] if self_rescue else PEACH[0]
    rescued: list[str] = []

    def dying(query):
        if query.kind == QueryKind.PEACH:
            wine_offers = [
                action
                for action in query.legal_actions
                if isinstance(action, UseCard) and action.card.type == "wine"
            ]
            assert [action.targets for action in wine_offers] == [(1,)], (
                "濒死自救的【酒】目标恒为濒死者自己"
            )
            if self_rescue:
                rescued.append("wine")
                return next(
                    action for action in wine_offers if action.card.card_id == expected
                )
            rescued.append("peach")
            return _find(
                query,
                lambda a: isinstance(a, UseCard) and a.card.card_id == expected,
            )
        return None

    players[1] = _Scripted(dying)
    _advance(engine, players, lambda e: bool(rescued) and e.state.player(1).hp == 1)

    assert engine.state.player(1).hp == 1, "自救 / 他人救都回复到 1"
    assert engine.state.player(1).wine_active is False, "濒死用法不产生加伤"


def test_near_death_wine_is_only_offered_to_the_dying_seat():
    """两种救法都先钉"他人不能对濒死者用【酒】"，再钉"濒死者自己可以"。

    ``self_rescue=True`` 用【酒】自救、``False`` 用【桃】救——后者证明濒死自-only 的过滤
    不会误伤【桃】的"他人对濒死者使用"路径。
    """
    for self_rescue in (True, False):
        _near_death_offer_case(self_rescue=self_rescue)


def test_near_death_wine_recovers_one_without_activating_the_bonus():
    wine = WINE[0]
    engine, players = _duel_into_near_death(hand=(wine, DUEL[0]))

    rescued: list[str] = []

    def dying(query):
        if query.kind == QueryKind.PEACH:
            offers = [
                action
                for action in query.legal_actions
                if isinstance(action, UseCard) and action.card.type == "wine"
            ]
            assert [action.targets for action in offers] == [(0,)]
            rescued.append("wine")
            return offers[0]
        return None

    players[0] = _Scripted(
        lambda q: _use(q, DUEL[0], 1) if q.kind == QueryKind.PLAY_PHASE else dying(q)
    )
    _advance(
        engine,
        players,
        lambda e: bool(rescued) and e.state.player(0).hp == 1 and not e.stack,
    )

    assert engine.state.player(0).hp == 1, "【酒】濒死自救回复 1 点"
    assert engine.state.player(0).wine_active is False, "濒死用法不产生加伤"
    assert _wine_changes(engine) == [], "濒死用法一条置位 delta 都不发"
    assert engine.state.player(0).card_use_counts.get("wine") == 1, "计数照记到同一个键"

    use = next(
        data
        for data in engine.payloads(FrameKind.USE)
        if data.get("card") is not None and data["card"].card_id == wine
    )
    assert use["use_timing"] is Timing.NEAR_DEATH_AT, "使用帧读得出这是求桃窗口开的使用"
    recover = engine.payloads(FrameKind.RECOVER)
    assert recover and recover[0]["card"].card_id == wine, "回复走既有 RECOVER 帧"
    assert _hp_changes(engine) == [(0, 1, 0), (0, 0, 1)], (
        "【决斗】扣到 0，【酒】回复到 1"
    )


def test_a_play_phase_wine_blocks_the_near_death_self_rescue_in_the_same_turn():
    """两种用法共用同一个计数键：出牌阶段用过 ⇒ 同一回合濒死时不再给【酒】。"""
    first, second = WINE[0], WINE[1]
    engine, players = _duel_into_near_death(hand=(first, second, DUEL[0]))
    used: list[str] = []

    def actor(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if "wine" not in used:
                action = _use(query, first, 0)
                if action is not None:
                    used.append("wine")
                    return action
            if "duel" not in used:
                action = _use(query, DUEL[0], 1)
                if action is not None:
                    used.append("duel")
                    return action
        if query.kind == QueryKind.RESPOND:
            return _find(query, lambda a: isinstance(a, Pass))
        return None

    players[0] = _Scripted(actor)
    _advance(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PEACH
        and e.pending.actor == 0,
    )

    query = engine.pending
    assert engine.state.player(0).hp == 0, "先进入濒死"
    assert engine.state.player(0).card_use_counts.get("wine") == 1
    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, UseCard) and action.card.type == "wine"
    ], "同一回合的第二次【酒】在濒死窗口里也不给候选"
    with pytest.raises(IllegalActionError):
        engine.submit(
            UseCard(card=engine.state.card(second), targets=(0,), answers_seq=query.seq)
        )


def test_a_near_death_self_rescue_uses_up_the_play_phase_wine_for_that_turn():
    """反向：濒死自救用掉唯一一次 ⇒ 恢复后的出牌阶段不再有【酒】候选。"""
    first, second = WINE[0], WINE[1]
    engine, players = _duel_into_near_death(hand=(first, second, DUEL[0]))
    rescued: list[str] = []

    def actor(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if "duel" not in rescued:
                action = _use(query, DUEL[0], 1)
                if action is not None:
                    rescued.append("duel")
                    return action
        if query.kind == QueryKind.RESPOND:
            return _find(query, lambda a: isinstance(a, Pass))
        if query.kind == QueryKind.PEACH:
            offers = [
                action
                for action in query.legal_actions
                if isinstance(action, UseCard) and action.card.type == "wine"
            ]
            assert offers, "第一次濒死时【酒】仍是合法候选"
            rescued.append("wine")
            return offers[0]
        return None

    players[0] = _Scripted(actor)
    _advance(
        engine,
        players,
        lambda e: "wine" in rescued
        and not e.stack
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE,
    )

    query = engine.pending
    assert engine.state.player(0).hp == 1, "自救成功、回到出牌阶段"
    assert engine.state.player(0).wine_active is False
    assert engine.state.player(0).card_use_counts.get("wine") == 1
    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, UseCard) and action.card.type == "wine"
    ], "同一个键：濒死自救用掉后，出牌阶段不能再喝"
    with pytest.raises(IllegalActionError):
        engine.submit(
            UseCard(card=engine.state.card(second), targets=(0,), answers_seq=query.seq)
        )


# ---------------------------------------------------------------------------
# 回合末清理 / 下回合可再用 / delta 往返 / 源码纪律
# ---------------------------------------------------------------------------


def test_a_wine_used_in_a_previous_turn_does_not_block_a_near_death_self_rescue():
    """spec §5.1 酒：「每回合一次」按**游戏回合**记——上一回合用过，不妨碍本回合濒死自救。

    P 在自己的出牌阶段喝【酒】、回合结束；Q 的回合里 P 被打到濒死：P 的 wine 计数必须
    已在**回合末**清掉（不是等 P 的下一个 ``TURN_START``），【酒】因此重新进自救候选；
    自救后计数记在 Q 的这个回合上；P 的下一个回合再清一次、【酒】又可用。
    """
    first, second, third = WINE[0], WINE[1], WINE[2]
    barbarian, slash = BARBARIAN[0], SLASH[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2, hand=(first, second, third)),
            SeatSpec(hand=(barbarian, slash)),
        ],
        deck=_deck_excluding(first, second, third, barbarian, slash),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    rescued: list[str] = []

    def p_side(query):
        if (
            query.kind == QueryKind.PLAY_PHASE
            and engine.state.player(0).card_use_counts.get("wine", 0) == 0
        ):
            action = _find(
                query, lambda a: isinstance(a, UseCard) and a.card.type == "wine"
            )
            if action is not None:
                return action
        if query.kind == QueryKind.RESPOND:
            return _find(query, lambda a: isinstance(a, Pass))
        return None

    used: list[str] = []

    def q_side(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if "barbarian" not in used:
                action = _use(query, barbarian, 0)
                if action is not None:
                    used.append("barbarian")
                    return action
            if "slash" not in used:
                action = _use(query, slash, 0)
                if action is not None:
                    used.append("slash")
                    return action
        if query.kind == QueryKind.PEACH:
            return _find(query, lambda a: isinstance(a, Pass))
        return None

    players = [_Scripted(p_side), _Scripted(q_side)]
    _advance(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PEACH
        and e.pending.actor == 0,
    )

    assert engine.state.player(0).hp == 0, "P 在 Q 的回合里进入濒死"
    assert engine.state.player(0).card_use_counts.get("wine", 0) == 0, (
        "P 上一回合的【酒】计数已在回合末清掉（不是等到 P 的下一个 TURN_START）"
    )
    offers = [
        action
        for action in engine.pending.legal_actions
        if isinstance(action, UseCard) and action.card.type == "wine"
    ]
    assert offers, "上一回合的酒不该挡住这一回合的濒死自救"
    assert {action.targets for action in offers} == {(0,)}, "自救目标恒为濒死者自己"
    engine.submit(offers[0])
    _advance(engine, players, lambda e: e.state.player(0).hp == 1 and not e.stack)

    assert engine.state.player(0).hp == 1, "跨回合濒死自救成功（回复 1）"
    assert engine.state.player(0).wine_active is False, "自救不产生加伤"
    assert engine.state.player(0).card_use_counts.get("wine") == 1, (
        "这一次自救记在 Q 的回合上，仍是每回合一次"
    )

    _advance(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    assert engine.state.player(0).card_use_counts.get("wine", 0) == 0, (
        "Q 的回合结束后计数再次清空"
    )
    assert [
        action
        for action in engine.pending.legal_actions
        if isinstance(action, UseCard) and action.card.type == "wine"
    ], "P 的下一个回合【酒】再次可用"


class _DrinkOncePerTurn:
    """每个自己的出牌阶段喝一张【酒】（按座位计数键为 0 才喝），其余最小应答。

    用 ``card_use_counts["wine"] == 0`` 判"本回合还没喝过"：它在自己的 ``TURN_START``
    被清空，因此跨回合自动复位，同一个出牌阶段里第二次询问则读到 1。
    """

    def __init__(self, engine: Engine, *, seat: int = 0) -> None:
        self.engine = engine
        self.seat = seat
        self.uses = 0

    def respond(self, query):
        if (
            query.kind == QueryKind.PLAY_PHASE
            and self.engine.state.player(self.seat).card_use_counts.get("wine", 0) == 0
        ):
            action = _find(
                query,
                lambda a: isinstance(a, UseCard) and a.card.type == "wine",
            )
            if action is not None:
                self.uses += 1
                return action
        return _minimal(query)


def test_wine_active_is_cleared_at_turn_end_and_usable_again_next_turn():
    from support import run_turns

    first, second = WINE[0], WINE[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second)), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    drinker = _DrinkOncePerTurn(engine)
    players = [drinker, _Scripted()]

    run_turns(engine, players, turns=1)
    assert drinker.uses == 1
    assert engine.state.player(0).wine_active is False, "回合末加伤必须清掉"
    assert "wine" not in engine.state.player(0).card_use_counts, (
        "「本回合已用」的记账也在回合末清掉（不等下一个 TURN_START）"
    )
    assert _wine_changes(engine) == [(0, True), (0, False)], (
        "置过而没用掉的那份在回合末由唯一写入口清掉（有 false delta）"
    )

    run_turns(engine, players, turns=3)
    assert drinker.uses == 2, "下个自己的回合计数已清空，【酒】可再用"
    assert engine.state.player(0).wine_active is False, "第二次回合末同样清掉"


def test_wine_change_is_a_recorded_delta_that_round_trips_through_the_journal(tmp_path):
    from sgs.engine import read_journal

    wine = WINE[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(wine,)), SeatSpec()],
        deck=_deck_excluding(wine),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    players = [_Scripted(), _Scripted()]
    _advance(engine, players, _at_play_phase(0))
    engine.submit(_use(engine.pending, wine, 0))
    _advance(engine, players, _at_play_phase(0))

    path = tmp_path / "wine.jsonl"
    path.write_text(engine.journal(), encoding="utf-8")
    records = read_journal(path)

    changes = [
        (record.seat, record.active)
        for record in records
        if record.kind == "delta" and record.delta_type == "wine_change"
    ]
    assert changes == [(0, True)]
    seqs = [record.seq for record in records if record.kind != "header"]
    assert seqs == sorted(seqs), "新 delta 不改记录流的 seq 单调性"


def test_the_engine_does_not_know_the_wine_card_name():
    """`engine/` 不认识牌名 ``"wine"``：加伤只经 ``effects.is_slash`` 判【杀】家族。"""
    import ast

    sources = sorted(ENGINE_ROOT.rglob("*.py"))
    assert len(sources) >= 4, "扫描目标可能指错了"

    offenders = [
        f"{path.name}:{node.lineno}"
        for path in sources
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and node.value == "wine"
    ]
    assert not offenders, "引擎侧出现了【酒】的牌名：" + "；".join(offenders)


def test_wine_uses_the_existing_damage_modifier_points_and_no_new_timing():
    """【酒】+1 住来源侧链：没有新增 Timing，也没有把属性 / 加伤写进 ``CardRef``。"""
    from sgs.state import CardRef
    from sgs.timing import Timing

    assert len(list(Timing)) == 83, "M5-04 不新增时机节点"
    assert not {"nature", "damage_type", "attribute", "element"} & set(CardRef.model_fields), (
        "加伤是效果不是牌面事实（ADR-003）"
    )
