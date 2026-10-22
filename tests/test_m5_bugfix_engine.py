"""M5 实机缺陷修复：借刀回票重问 / 雌雄误触发火攻 / 铁索按选择顺序结算。

落点 = 库门面（与 M5 各票同姿态）：``Engine.from_state`` 的人为局面 + ``Engine.submit``
驱动 + 记录流 / 帧载荷，不碰内部回调。
"""

from __future__ import annotations

import sgs.effects
from sgs.data import Gender, Identity, RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    QueryKind,
    SelectCards,
    UseCard,
    WindowCause,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_ex_160")

XIAOJI = "hero.sunshangxiang.xiaoji"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _minimal(query):
    """最小合法应答：出牌阶段结束、响应 / 濒死放弃、其余取第一个候选。"""
    if query.kind == QueryKind.PLAY_PHASE:
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _Scripted:
    """一个座位：先跑 ``handler``，返回 ``None`` 时退到最小应答。"""

    def __init__(self, handler=None) -> None:
        self.handler = handler

    def respond(self, query):
        if self.handler is not None:
            action = self.handler(query)
            if action is not None:
                return action
        return _minimal(query)


def _queries(engine, kind: QueryKind) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


# ---------------------------------------------------------------------------
# Bug A：借刀 + 交出武器 + 枭姬——重入后不得再问持刀者
# ---------------------------------------------------------------------------


def test_borrowed_sword_handover_does_not_reask_the_holder_after_xiaoji():
    """实机：孙尚香交出青龙偃月刀触发【枭姬】后，借刀**又问了一次**「出杀 / 交出武器」。

    持有武器与一张手牌【杀】⇒ 持刀者窗口的 a 支可行，窗口真的发出来；她答 ``Pass``
    （交出武器）⇒ 武器进使用者手牌、失去装备触发【枭姬】摸两张。枭姬的询问把这次效果
    挂起，重入时**已经答过的窗口不得再问**——持刀者的决定必须作为帧私有进度记下来。
    """
    borrowed = _cards("borrowed_sword")[0]
    sword = _cards("green_dragon_blade")[0]
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(slash,), equip=(sword,), hero="sunshangxiang"),
        ],
        deck=_deck_excluding(borrowed, sword, slash),
        turn_seat=0,
    )
    used: list[int] = []

    def user(query):
        if query.kind == QueryKind.PLAY_PHASE and not used:
            for action in getattr(query, "designation_actions", ()):
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == borrowed
                    and action.targets == (1,)
                    and action.designations == (0,)
                ):
                    used.append(action.card.card_id)
                    return action
        return None

    def holder(query):
        if query.kind == QueryKind.RESPOND and query.cause is WindowCause.BORROWED_SWORD:
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == XIAOJI:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill):
                    return action
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(user), _Scripted(holder)],
        lambda e: borrowed in e.state.discard and not e.stack,
    )

    holder_windows = [
        query
        for query in _queries(engine, QueryKind.RESPOND)
        if query.cause is WindowCause.BORROWED_SWORD
    ]
    assert len(holder_windows) == 1, (
        "持刀者的「出杀 / 交出武器」窗口恰好一次：枭姬挂起后的重入不得重问（实机缺陷）"
    )
    assert holder_windows[0].actor == 1
    assert ("equip", "hand", "LOSE") in _moves_of(engine, sword), "武器交给使用者"
    assert sword in engine.state.player(0).hand
    assert sword not in engine.state.player(1).equip


def test_borrowed_sword_handover_completes_xiaoji_draw_and_the_use():
    """同一次借刀：枭姬摸两张真的落地，借刀本体照常结算完（进弃牌堆）。"""
    borrowed = _cards("borrowed_sword")[0]
    sword = _cards("green_dragon_blade")[0]
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(slash,), equip=(sword,), hero="sunshangxiang"),
        ],
        deck=_deck_excluding(borrowed, sword, slash),
        turn_seat=0,
    )
    used: list[int] = []

    def user(query):
        if query.kind == QueryKind.PLAY_PHASE and not used:
            for action in getattr(query, "designation_actions", ()):
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == borrowed
                    and action.targets == (1,)
                    and action.designations == (0,)
                ):
                    used.append(action.card.card_id)
                    return action
        return None

    def holder(query):
        if query.kind == QueryKind.RESPOND and query.cause is WindowCause.BORROWED_SWORD:
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == XIAOJI:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill):
                    return action
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(user), _Scripted(holder)],
        lambda e: borrowed in e.state.discard and not e.stack,
    )

    position = next(
        index
        for index, record in enumerate(engine.records)
        if record.kind == "timing"
        and record.timing is Timing.MOVE_AFTER
        and [card.card_id for card in record.cards] == [sword]
    )
    draws = [
        card.card_id
        for record in engine.records[position + 1 :]
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DRAW"
        and record.to_zone.zone.value == "hand"
        and record.to_zone.seat == 1
        for card in record.cards
    ]
    assert len(draws) == 2, "枭姬摸两张"
    assert borrowed in engine.state.discard, "借刀这次使用照常结算完"
    assert slash in engine.state.player(1).hand, "持刀者的【杀】没有被用掉（她选了交出武器）"


# ---------------------------------------------------------------------------
# Bug B：雌雄双股剑只对【杀】触发——火攻不得进那个选择
# ---------------------------------------------------------------------------


def _suit_cards(suit_name: str, *, exclude: tuple[int, ...] = ()) -> list[int]:
    excluded = set(exclude)
    return [
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit is not None
        and card.suit.value == suit_name
        and index not in excluded
    ]


def _select_of(query, card_id: int):
    for action in query.legal_actions:
        if isinstance(action, SelectCards) and len(action.picks) == 1:
            slot = query.slots[action.picks[0]]
            if slot.card is not None and slot.card.card_id == card_id:
                return action
    return None


def _delta_records(engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def test_double_sword_does_not_trigger_on_fire_attack():
    """实机：持【雌雄双股剑】用【火攻】指定异性目标时，双股剑的选择**不得**出现。

    只有【杀】（含火 / 雷 / 转化产物）才让雌雄触发；火攻是普通锦囊，必须照常结算：
    目标亮牌 → 使用者弃同花色 → 1 点火焰伤害。
    """
    DOUBLE_SWORD = _cards("double_sword")[0]
    fire_attack = _cards("fire_attack")[0]
    spade_a = _suit_cards("spade", exclude=(fire_attack,))[0]
    spade_b = _suit_cards("spade", exclude=(fire_attack, spade_a))[0]
    heart_a = _suit_cards("heart", exclude=(fire_attack, spade_a, spade_b))[0]
    heart_b = _suit_cards("heart", exclude=(fire_attack, spade_a, spade_b, heart_a))[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hand=(fire_attack, spade_a),
                equip=(DOUBLE_SWORD,),
                gender=Gender.MALE,
            ),
            SeatSpec(hand=(spade_b,), gender=Gender.FEMALE),
        ],
        deck=(heart_a, heart_b, *_deck_excluding(
            fire_attack, spade_a, spade_b, heart_a, heart_b, DOUBLE_SWORD
        )),
        turn_seat=0,
    )

    def user(query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == fire_attack
                    and action.targets == (1,)
                ):
                    return action
            return None
        if query.kind == QueryKind.SKILL_ACTIVATION:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill):
                    return action
            return None
        if query.kind == QueryKind.SELECT_CARDS and query.actor == 0:
            return _select_of(query, spade_a)
        return None

    def target(query):
        if query.kind == QueryKind.SELECT_CARDS and query.actor == 1:
            return _select_of(query, spade_b)
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(user), _Scripted(target)],
        lambda e: fire_attack in e.state.discard and not e.stack,
    )

    assert not [
        query
        for query in _queries(engine, QueryKind.SKILL_ACTIVATION)
        if getattr(query, "skill_id", None) == "equip.double_sword"
    ], "火攻不是【杀】⇒ 雌雄连发动询问都不发"
    option_queries = [
        query
        for query in _queries(engine, QueryKind.SELECT_OPTION)
        if getattr(query, "skill_id", None) == "equip.double_sword"
    ]
    assert option_queries == [], "火攻不得开出「弃一张 / 令使用者摸一张」的选择"
    reveals = _delta_records(engine, "card_reveal")
    assert [(record.seat, record.card.card_id) for record in reveals] == [(1, spade_b)], (
        "火攻照常结算：目标亮出那张手牌"
    )
    assert [(record.seat, record.before, record.after) for record in _delta_records(engine, "hp_change")] == [
        (1, 4, 3)
    ], "同花色弃牌 ⇒ 1 点火焰伤害照常落地"
    assert spade_b in engine.state.player(1).hand, "亮牌不移动：牌仍在目标手里"


def test_double_sword_still_triggers_on_a_slash_against_an_opposite_gender_target():
    """反面对照：同一局面换成【杀】，雌雄的选择照旧（目标弃一张 / 令使用者摸一张）。"""
    DOUBLE_SWORD = _cards("double_sword")[0]
    slash = _cards("slash")[0]
    filler = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(hand=(filler,), gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(slash, filler, DOUBLE_SWORD),
        turn_seat=0,
    )

    def user(query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (1,)
                ):
                    return action
            return None
        if query.kind == QueryKind.SKILL_ACTIVATION:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill):
                    return action
            return None
        return None

    def target(query):
        if query.kind == QueryKind.DODGE:
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if query.kind == QueryKind.SELECT_OPTION:
            for action in query.legal_actions:
                if getattr(action, "choice", None) == "draw_one":
                    return action
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(user), _Scripted(target)],
        lambda e: slash in e.state.discard and not e.stack,
    )

    options = [
        query
        for query in _queries(engine, QueryKind.SELECT_OPTION)
        if getattr(query, "skill_id", None) == "equip.double_sword"
    ]
    assert len(options) == 1, "【杀】指定异性目标 ⇒ 双股剑的选择照旧发一次"
    assert options[0].actor == 1, "选择权在目标"
    assert options[0].options == ("discard_one", "draw_one")


# ---------------------------------------------------------------------------
# Bug C：铁索连环按使用者选择顺序结算；多目标【杀】仍从当前回合角色起逆时针
# ---------------------------------------------------------------------------


def _eight_seat_state(*, hand=(), equip=()):
    used = (*hand, *equip)
    seats = [
        SeatSpec(
            hand=hand if index == 0 else (),
            equip=equip if index == 0 else (),
            identity=Identity.LORD if index == 0 else Identity.REBEL,
        )
        for index in range(8)
    ]
    return build_state(
        pack=PACK,
        seats=seats,
        deck=_deck_excluding(*used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=8),
    )


def test_iron_chain_resolves_in_the_order_the_user_selected():
    """实机：铁索目标选 4 再选 3 ⇒ 引擎曾改排成 3、4（从回合角色起逆时针）。

    铁索的特殊规则是“按使用者选择顺序结算”（spec §5.1.2）；多目标【杀】才走
    “从当前回合角色起、逆时针”。这里在**公开的 Engine 帧缝**上钉住：``start_use``
    收到的选择序 ``(4, 3)`` 原样进 USE 帧，结算时的 ``chained_change`` 也按 4 → 3 落地。
    """
    chain = _cards("iron_chain")[0]
    state = _eight_seat_state(hand=(chain,))
    engine = Engine.from_state(state, seed=1)

    frame = engine.start_use(state.card(chain), source=0, targets=(4, 3))
    assert frame.kind is FrameKind.USE
    assert frame.data["targets"] == (4, 3), "铁索保留使用者的选择顺序，不被逆时针重排"

    run_until(
        engine,
        [_Scripted() for _ in range(8)],
        lambda e: chain in e.state.discard and not e.stack,
    )
    changes = [
        (record.seat, record.chained) for record in _delta_records(engine, "chained_change")
    ]
    assert changes == [(4, True), (3, True)], "结算顺序 = 选择顺序（4 先、3 后）"


def test_a_multi_target_slash_still_resolves_counterclockwise():
    """反面对照：【方天画戟】的多目标【杀】仍从当前回合角色起逆时针（(4,3) → (3,4)）。"""
    halberd = _cards("halberd")[0]
    slash = _cards("slash")[0]
    state = _eight_seat_state(hand=(slash,), equip=(halberd,))
    combos = [
        action.targets
        for action in sgs.effects.legal_actions_for(
            state, 0, QueryKind.PLAY_PHASE, answers_seq=1
        )
        if isinstance(action, UseCard) and action.card.card_id == slash
    ]
    assert (3, 4) in combos, "方天画戟：最后一张手牌【杀】可指定 3、4"

    engine = Engine.from_state(state, seed=1)
    frame = engine.start_use(state.card(slash), source=0, targets=(4, 3))
    assert frame.data["targets"] == (3, 4), (
        "多目标【杀】没有“按选择顺序”的牌面声明 ⇒ 仍按逆时针排序"
    )
