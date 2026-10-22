"""M5-05b：朱雀羽扇（普通【杀】→ 火【杀】）+ 【杀】族通用判据。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流 + ``legal_actions_for``
的候选集合 + ``sgs.effects`` 的只读判据。军争牌只在 ``standard_ex_160`` 档里，故每个
用例都显式选这一档（档位纪律）。

本票三块：

- **朱雀羽扇**：``equip.vermilion_feather_fan`` 的 ``CANDIDATES`` 回调走 W7 的单源转化
  （``single_conversion``），产物牌型 ``fire_slash``——属性住**产物牌型**的行为表（D2），
  故 FIRE 伤害不需要第二套声明；
- **火【杀】命名空间**：见 ``tests/test_virtual_ids.py``（本票只追加
  ``VirtualKind.FIRE_SLASH``）；
- **【杀】族通用判据**：接缝 / 响应窗口 / ``_own_slash`` 三处都改读
  :func:`~sgs.effects.is_slash`（共享 ``count_key``），不写具体牌名分支；藤甲仍是
  M5-05a 的 **normal-only** 判据，本票只补回归断言。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Color, RuleConfig, color_of_suit, load_content_pack
from sgs.effects import (
    DamageNature,
    attack_range,
    check_count,
    damage_amount,
    legal_actions_for,
    target_is_effective,
)
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    PlayCard,
    QueryKind,
    RespondContext,
    RespondMode,
    SelectCards,
    UseCard,
    WindowCause,
)
from support import SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_ex_160")

SLASH = cards_of_type(PACK, "slash")
FIRE_SLASH = cards_of_type(PACK, "fire_slash")
THUNDER_SLASH = cards_of_type(PACK, "thunder_slash")
PEACH = cards_of_type(PACK, "peach")
DODGE = cards_of_type(PACK, "dodge")
DUEL = cards_of_type(PACK, "duel")
NULLIFY = cards_of_type(PACK, "nullify")

FAN = cards_of_type(PACK, "vermilion_feather_fan")
GREEN_DRAGON = cards_of_type(PACK, "green_dragon_blade")
AXE = cards_of_type(PACK, "axe")
SERPENT = cards_of_type(PACK, "serpent_spear")
CROSSBOW = cards_of_type(PACK, "crossbow")
QINGGANG = cards_of_type(PACK, "qinggang_sword")
RENWANG = cards_of_type(PACK, "renwang_shield")
VINE = cards_of_type(PACK, "vine_armor")

RED_PEACH = next(
    card_id for card_id in PEACH if color_of_suit(PACK.deck.cards[card_id].suit) is Color.RED
)
BLACK_SLASH = next(
    card_id
    for card_id in SLASH
    if color_of_suit(PACK.deck.cards[card_id].suit) is Color.BLACK
)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _specs_used(specs) -> list[int]:
    return [card for spec in specs for card in (*spec.hand, *spec.equip, *spec.judge)]


def _state(specs, *, turn_seat: int = 0, deck=None):
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck if deck is not None else _deck_excluding(*_specs_used(specs)),
        turn_seat=turn_seat,
        rule_config=RuleConfig(first_seat=0),
    )


# ---------------------------------------------------------------------------
# 候选 / 记录流的读取小工具（全部走公开缝）
# ---------------------------------------------------------------------------


def _respond(
    required: str,
    *,
    mode: RespondMode = RespondMode.PLAY,
    targets: tuple[int, ...] = (),
    cause: WindowCause = WindowCause.BARBARIAN_INVASION,
) -> RespondContext:
    return RespondContext(cause=cause, required=required, mode=mode, use_targets=targets)


def _respond_candidates(state, seat: int, payload: RespondContext):
    return legal_actions_for(
        state, seat, QueryKind.RESPOND, answers_seq=1, payload=payload
    )


def _play_candidates(state, seat: int = 0):
    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


def _virtual(actions, type_id: str) -> list[Any]:
    """候选里该牌名的**虚拟牌**行动（使用与打出两种构造子都算）。"""
    return [
        action
        for action in actions
        if isinstance(action, (UseCard, PlayCard))
        and action.card.is_virtual
        and action.card.type == type_id
    ]


def _virtual_sources(actions, type_id: str) -> set[tuple[int, ...]]:
    return {action.card.virtual_sources for action in _virtual(actions, type_id)}


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _end_or_pass(query):
    end = _find(query, lambda action: isinstance(action, EndPhase))
    if end is not None:
        return end
    return _find(query, lambda action: isinstance(action, Pass))


class _Scripted:
    """把一个 ``respond(query)`` 函数包成 :func:`run_until` 认的参与者。"""

    def __init__(self, handler) -> None:
        self.handler = handler

    def respond(self, query):
        return self.handler(query)


class _FrameSpy(Engine):
    """记下每一次压栈的帧类型与载荷（``nature`` 这类帧私有事实只能这样观测）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pushed: list[tuple[Any, dict[str, Any]]] = []

    def push_frame(self, kind, data, *, timing):  # type: ignore[no-untyped-def]
        frame = super().push_frame(kind, data, timing=timing)
        self.pushed.append((kind, dict(data)))
        return frame


def _damage_payloads(engine: _FrameSpy) -> list[dict]:
    return [data for kind, data in engine.pushed if kind is FrameKind.DAMAGE]


def _use_chain_done(engine) -> bool:
    popped_use = any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.USE
        for record in engine.records
    )
    return popped_use and not engine.stack


# ---------------------------------------------------------------------------
# 朱雀羽扇：出牌阶段的单源转化（普通【杀】→ 火【杀】）
# ---------------------------------------------------------------------------


def test_the_fan_offers_a_normal_slash_as_a_fire_slash_in_the_play_phase():
    """普通【杀】进候选、产物是火【杀】；单源继承原牌的花色 / 点数 / 颜色。"""
    state = _state(
        [
            SeatSpec(
                hand=(SLASH[0], FIRE_SLASH[0], THUNDER_SLASH[0], PEACH[0]),
                equip=(FAN[0],),
            ),
            SeatSpec(),
        ]
    )
    origin = state.card(SLASH[0])

    virtual = _virtual(_play_candidates(state), "fire_slash")

    assert virtual, "装备朱雀羽扇且手里有普通【杀】⇒ 追加火【杀】候选"
    assert _virtual_sources(_play_candidates(state), "fire_slash") == {(SLASH[0],)}, (
        "只有普通【杀】作来源：火杀 / 雷杀 / 桃都不是朱雀羽扇的源"
    )
    product = virtual[0].card
    assert product.is_virtual and product.card_id < 0
    assert product.name == "火杀"
    assert (product.suit, product.rank, product.color) == (
        origin.suit,
        origin.rank,
        origin.color,
    ), "单源转化继承来源牌的花色 / 点数 / 颜色"


def test_the_fan_converts_only_normal_slashes_never_fire_or_thunder():
    """源筛选是**普通【杀】**：属性【杀】与其它牌都不进来源集合（成对反例）。"""
    state = _state(
        [
            SeatSpec(
                hand=(FIRE_SLASH[0], THUNDER_SLASH[0], PEACH[0], SLASH[0]),
                equip=(FAN[0],),
            ),
            SeatSpec(),
        ]
    )

    sources = _virtual_sources(_play_candidates(state), "fire_slash")

    assert sources == {(SLASH[0],)}
    assert (FIRE_SLASH[0],) not in sources, (
        "火【杀】不能再被羽扇转化：产物是火杀，来源必须是普通杀"
    )
    assert (THUNDER_SLASH[0],) not in sources
    assert (PEACH[0],) not in sources


def test_the_fire_slash_product_has_attack_range_four():
    """羽毛扇的攻击范围 4 在牌面数据里（M5-01a），火【杀】候选因此够得着距离 3。"""
    state = _state(
        [SeatSpec(hand=(SLASH[0],), equip=(FAN[0],))] + [SeatSpec() for _ in range(5)]
    )

    assert attack_range(state, 0) == 4
    combinations = {
        action.targets
        for action in _virtual(_play_candidates(state), "fire_slash")
        if isinstance(action, UseCard)
    }

    assert (3,) in combinations, "6 人桌上面向座位 3 的距离是 3 ≤ 攻击范围 4"


FAN_SKILL = "equip.vermilion_feather_fan"


def _confirm_fan_preactivation(query):
    """出牌阶段按**预发动清单**确认朱雀羽扇（与 H 族转化同一条接入路径，W4-fix-2）。

    来源取清单来源步的第一个选项、目标取目标池的第一项——与界面"点技能 → 选牌 →
    选目标 → 确认"同形，交出去的是 ``ActivateSkill(来源, 目标)``。
    """
    if query.kind not in (QueryKind.PLAY_PHASE, QueryKind.RESPOND):
        return None
    steps = query.transform_preactivations.get(FAN_SKILL)
    if not steps:
        return None
    source = int(steps[0].pool[0].options[0])
    choices: tuple[str, ...] = ()
    if len(steps) > 1:
        seat = steps[1].pool[0].options[0].removeprefix("seat:")
        choices = (f"seat:{seat}",)
    return ActivateSkill(
        skill_id=FAN_SKILL, picks=(source,), choices=choices, answers_seq=query.seq
    )


def test_the_fan_product_deals_fire_damage_and_sends_the_source_to_the_discard_pile():
    """产物火【杀】按产物牌型的行为表结算：FIRE 伤害、来源实体【杀】进弃牌堆。"""
    state = _state([SeatSpec(hand=(SLASH[0],), equip=(FAN[0],)), SeatSpec()])
    engine = _FrameSpy.from_state(state, seed=1)

    def attacker(query):
        action = _confirm_fan_preactivation(query)
        if action is not None:
            return action
        return _end_or_pass(query)

    run_until(engine, [_Scripted(attacker), _EndPhaseOrPass()], _use_chain_done)

    damage = _damage_payloads(engine)
    assert len(damage) == 1, "一次火【杀】恰好压出一个伤害帧"
    assert damage[0]["nature"] is DamageNature.FIRE, "产物是本票的 FIRE 声明"
    assert damage[0]["chain"] is False
    assert damage[0]["channel"].type == "fire_slash"
    assert damage[0]["channel"].is_virtual
    assert damage[0]["channel"].virtual_sources == (SLASH[0],)
    assert SLASH[0] in engine.state.discard, "来源实体【杀】走既有虚拟源移动路径进弃牌堆"
    assert engine.state.player(1).hp == 3


class _EndPhaseOrPass:
    """最小合法应答：出牌阶段结束、响应与濒死放弃、其余取第一个候选。"""

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _end_or_pass(query)
        pass_action = _find(query, lambda action: isinstance(action, Pass))
        if pass_action is not None:
            return pass_action
        return _end_or_pass(query)


def test_the_fan_product_is_respondable_to_barbarian_duel_and_borrowed_sword():
    """打出的两种语境都覆盖：南蛮 / 决斗（PLAY）与借刀（USE，目标固定为 Bn）。"""
    state = _state(
        [
            SeatSpec(),
            SeatSpec(
                hand=(SLASH[0], FIRE_SLASH[0], THUNDER_SLASH[0], PEACH[0]),
                equip=(FAN[0],),
            ),
            SeatSpec(),
        ]
    )

    barbarian = _respond_candidates(state, 1, _respond("slash"))
    duel = _respond_candidates(state, 1, _respond("slash", cause=WindowCause.DUEL))
    borrowed = _respond_candidates(
        state,
        1,
        _respond(
            "slash",
            mode=RespondMode.USE,
            targets=(2,),
            cause=WindowCause.BORROWED_SWORD,
        ),
    )

    for actions, cause in ((barbarian, WindowCause.BARBARIAN_INVASION), (duel, WindowCause.DUEL)):
        virtual = _virtual(actions, "fire_slash")
        assert virtual and isinstance(virtual[0], PlayCard), (
            f"{cause} 窗口里羽扇产物是**打出**（PlayCard）"
        )
        assert virtual[0].card.virtual_sources == (SLASH[0],)

    borrowed_virtual = _virtual(borrowed, "fire_slash")
    assert borrowed_virtual and isinstance(borrowed_virtual[0], UseCard)
    assert borrowed_virtual[0].targets == (2,), "借刀窗口固定 Bn=2"
    assert borrowed_virtual[0].card.virtual_sources == (SLASH[0],)


def test_the_fan_conversion_can_be_confirmed_in_the_barbarian_response_window():
    """响应窗口的转化也走**预发动清单**（W4-fix-2）：羽扇在南蛮窗口确认后打出火【杀】。"""
    barbarian = cards_of_type(PACK, "barbarian_invasion")[0]
    state = _state(
        [
            SeatSpec(hand=(barbarian,)),
            SeatSpec(hand=(SLASH[0],), equip=(FAN[0],)),
            SeatSpec(),
        ]
    )
    engine = Engine.from_state(state, seed=1)

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return (
                _find(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.card_id == barbarian,
                )
                or _end_or_pass(query)
            )
        return _end_or_pass(query)

    def confirm(query):
        action = _confirm_fan_preactivation(query)
        if action is not None:
            return action
        return _end_or_pass(query)

    run_until(
        engine,
        [_Scripted(attacker), _Scripted(confirm), _EndPhaseOrPass()],
        _use_chain_done,
    )

    played = [
        record.action
        for record in engine.records
        if record.kind == "action"
        and isinstance(record.action, (UseCard, PlayCard))
        and record.action.card.type == "fire_slash"
        and record.action.card.is_virtual
    ]
    assert played, "南蛮窗口里按清单确认的羽扇产物必须被打出（火【杀】）"
    assert engine.state.player(1).hp == 4, "按清单打出的火【杀】免除了南蛮的伤害"
    assert engine.state.player(2).hp == 3, "没打出的座位照常受伤"
    assert SLASH[0] in engine.state.discard, "来源实体【杀】进弃牌堆"


def test_the_fan_does_not_change_the_products_of_wusheng_longdan_or_the_serpent_spear():
    """武圣 / 龙胆 / 丈八蛇矛的产物仍是普通【杀】（不带属性）——羽扇只加自己的产物。"""
    wusheng = _state(
        [
            SeatSpec(
                hand=(RED_PEACH, BLACK_SLASH),
                equip=(FAN[0],),
                hero="guanyu",
            ),
            SeatSpec(),
        ]
    )
    assert _virtual_sources(_play_candidates(wusheng), "slash") == {
        (RED_PEACH,),
        (FAN[0],),
    }, (
        "武圣的红牌产物是普通【杀】（红桃【桃】与红色装备按区域默认都是合法来源）"
    )
    assert _virtual_sources(_play_candidates(wusheng), "fire_slash") == {
        (BLACK_SLASH,)
    }, "羽扇只吃普通【杀】——武圣的产物不会被二次转成火【杀】"

    longdan = _state(
        [
            SeatSpec(
                hand=(DODGE[0], SLASH[0]),
                equip=(FAN[0],),
                hero="zhaoyun",
            ),
            SeatSpec(),
        ]
    )
    assert _virtual_sources(_play_candidates(longdan), "slash") == {(DODGE[0],)}, (
        "龙胆的【闪】→【杀】产物仍是普通【杀】"
    )
    assert _virtual_sources(_play_candidates(longdan), "fire_slash") == {(SLASH[0],)}, (
        "同一张【杀】：龙胆把它转成【闪】，羽扇把它转成火【杀】，两条改写各自独立"
    )

    serpent = _state(
        [
            SeatSpec(hand=(PEACH[0], PEACH[1]), equip=(SERPENT[0],)),
            SeatSpec(),
        ]
    )
    assert _virtual_sources(_play_candidates(serpent), "slash") == {
        tuple(sorted((PEACH[0], PEACH[1])))
    }
    assert not _virtual(_play_candidates(serpent), "fire_slash"), (
        "丈八蛇矛的产物是普通【杀】，不会变成火【杀】"
    )


# ---------------------------------------------------------------------------
# 【杀】族通用判据 ①：被【闪】抵消的接缝（青龙偃月刀 / 贯石斧）
# ---------------------------------------------------------------------------


def _seam_player(used_card: int, provide: int | None = None):
    """出牌阶段用 ``used_card``；接缝发动；必要时提供 ``provide`` 那张【杀】。"""

    def respond(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return (
                _find(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.card_id == used_card
                    and action.targets == (1,),
                )
                or _end_or_pass(query)
            )
        if query.kind == QueryKind.SKILL_ACTIVATION:
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == QueryKind.SELECT_CARDS:
            if provide is not None:
                for index, slot in enumerate(query.slots):
                    if slot.card is not None and slot.card.card_id == provide:
                        action = _find(
                            query,
                            lambda candidate, i=index: isinstance(candidate, SelectCards)
                            and candidate.picks == (i,),
                        )
                        if action is not None:
                            return action
            action = _find(query, lambda candidate: isinstance(candidate, SelectCards))
            if action is not None:
                return action
        return _end_or_pass(query)

    return respond


class _DodgeOnce:
    """第一次【闪】响应打出一张【闪】，之后放弃。"""

    def __init__(self) -> None:
        self.used = False

    def respond(self, query):
        if query.kind == QueryKind.DODGE and not self.used:
            action = _find(
                query,
                lambda candidate: isinstance(candidate, UseCard)
                and candidate.card.type == "dodge",
            )
            if action is not None:
                self.used = True
                return action
        return _end_or_pass(query)


def _has_activation(engine, skill_id: str) -> bool:
    return any(
        record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and record.query.skill_id == skill_id
        for record in engine.records
    )


def test_fire_and_thunder_slashes_open_the_green_dragon_seam():
    """火【杀】/ 雷【杀】被【闪】抵消 ⇒ 青龙偃月刀接缝照开（与普通【杀】同形）。"""
    for type_id in ("fire_slash", "thunder_slash"):
        used = cards_of_type(PACK, type_id)[0]
        state = _state(
            [
                SeatSpec(hand=(used, SLASH[1]), equip=(GREEN_DRAGON[0],)),
                SeatSpec(hand=(DODGE[0],)),
            ]
        )
        engine = Engine.from_state(state, seed=1)

        run_until(
            engine,
            [_Scripted(_seam_player(used, provide=SLASH[1])), _DodgeOnce()],
            _use_chain_done,
        )

        assert _has_activation(engine, "equip.green_dragon_blade"), (
            f"{type_id} 被【闪】抵消后应进青龙偃月刀接缝"
        )
        assert engine.state.player(1).hp == 3, "续杀命中，造成 1 点伤害"


def test_fire_and_thunder_slashes_open_the_axe_seam():
    """火【杀】/ 雷【杀】被【闪】抵消 ⇒ 贯石斧接缝照开（弃两张继续造成伤害）。"""
    for type_id in ("fire_slash", "thunder_slash"):
        used = cards_of_type(PACK, type_id)[0]
        state = _state(
            [
                SeatSpec(hand=(used, PEACH[0], PEACH[1]), equip=(AXE[0],)),
                SeatSpec(hand=(DODGE[0],)),
            ]
        )
        engine = Engine.from_state(state, seed=1)

        run_until(engine, [_Scripted(_seam_player(used)), _DodgeOnce()], _use_chain_done)

        assert _has_activation(engine, "equip.axe"), (
            f"{type_id} 被【闪】抵消后应进贯石斧接缝"
        )
        assert engine.state.player(1).hp == 3, "贯石斧跳『生效时』照常造成伤害"


def test_a_non_slash_card_does_not_open_the_slash_canceled_seam():
    """非【杀】的牌被抵消（决斗被无懈）⇒ 青龙 / 贯石斧接缝都不开。"""
    state = _state(
        [
            SeatSpec(hand=(DUEL[0], PEACH[0], PEACH[1]), equip=(AXE[0],)),
            SeatSpec(hand=(NULLIFY[0],)),
        ]
    )
    engine = Engine.from_state(state, seed=1)

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return (
                _find(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.card_id == DUEL[0]
                    and action.targets == (1,),
                )
                or _end_or_pass(query)
            )
        if query.kind == QueryKind.RESPOND:
            return _end_or_pass(query)
        return _end_or_pass(query)

    def defender(query):
        if query.kind == QueryKind.RESPOND and query.required == "nullify":
            return (
                _find(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.type == "nullify",
                )
                or _end_or_pass(query)
            )
        return _end_or_pass(query)

    run_until(engine, [_Scripted(attacker), _Scripted(defender)], _use_chain_done)

    assert not _has_activation(engine, "equip.axe")
    assert not _has_activation(engine, "equip.green_dragon_blade")
    assert engine.state.player(0).hp == 4, "决斗已被无懈，不产生伤害"


# ---------------------------------------------------------------------------
# 【杀】族通用判据 ②：南蛮 / 决斗 / 借刀的响应窗口（effects/actions._required_hand）
# ---------------------------------------------------------------------------


def test_fire_and_thunder_slashes_can_respond_to_barbarian_duel_and_borrowed_sword():
    """火【杀】/ 雷【杀】出现在三种"要求打出【杀】"的窗口；非【杀】不出现。"""
    state = _state(
        [
            SeatSpec(),
            SeatSpec(hand=(FIRE_SLASH[0], THUNDER_SLASH[0], PEACH[0])),
            SeatSpec(),
        ]
    )

    for payload in (
        _respond("slash"),
        _respond("slash", cause=WindowCause.DUEL),
    ):
        actions = _respond_candidates(state, 1, payload)
        played = {
            action.card.card_id for action in actions if isinstance(action, PlayCard)
        }
        assert {FIRE_SLASH[0], THUNDER_SLASH[0]} <= played, payload.cause
        assert PEACH[0] not in played, "非【杀】不进需要【杀】的窗口"

    borrowed = _respond_candidates(
        state,
        1,
        _respond(
            "slash",
            mode=RespondMode.USE,
            targets=(2,),
            cause=WindowCause.BORROWED_SWORD,
        ),
    )
    used = {
        action.card.card_id for action in borrowed if isinstance(action, UseCard)
    }
    assert {FIRE_SLASH[0], THUNDER_SLASH[0]} <= used, (
        "借刀（使用【杀】）同样接受属性【杀】"
    )
    assert PEACH[0] not in used


def test_the_family_rule_does_not_leak_into_other_required_card_names():
    """``required`` 的放宽只限【杀】族：【闪】/ 其它牌名仍逐字相等（精确过滤）。"""
    state = _state(
        [
            SeatSpec(),
            SeatSpec(hand=(FIRE_SLASH[0], THUNDER_SLASH[0], DODGE[0])),
            SeatSpec(),
        ]
    )

    dodge_window = _respond_candidates(
        state, 1, _respond("dodge", cause=WindowCause.ARROW_BARRAGE)
    )
    played = {
        action.card.card_id for action in dodge_window if isinstance(action, PlayCard)
    }
    assert played == {DODGE[0]}, "需要【闪】时火杀 / 雷杀不得混进来"


# ---------------------------------------------------------------------------
# 【杀】族通用判据 ③：装备门控（skills/catalog._own_slash）
# ---------------------------------------------------------------------------


def test_the_crossbow_lifts_the_slash_limit_for_fire_and_thunder_slashes():
    """诸葛连弩的"无次数限制"对火【杀】/ 雷【杀】生效（共享计数键）。"""
    armed = _state(
        [
            SeatSpec(hand=(FIRE_SLASH[0], THUNDER_SLASH[0]), equip=(CROSSBOW[0],)),
            SeatSpec(),
        ]
    )
    bare = _state([SeatSpec(hand=(FIRE_SLASH[0], THUNDER_SLASH[0])), SeatSpec()])

    armed.player(0).card_use_counts["slash"] = 3
    bare.player(0).card_use_counts["slash"] = 1

    for card_id in (FIRE_SLASH[0], THUNDER_SLASH[0]):
        assert check_count(armed, 0, armed.card(card_id)) is False, (
            "连弩下火杀 / 雷杀都无次数限制"
        )
        assert check_count(bare, 0, bare.card(card_id)) is True, (
            "对照：无连弩时属性【杀】与普通【杀】共用每回合一次的上限"
        )


def test_the_qinggang_sword_ignores_armor_for_fire_and_thunder_slashes():
    """青釭剑的"无视防具"对属性【杀】生效：越过仁王盾 / 藤甲火焰 +1。"""
    state = _state(
        [
            SeatSpec(equip=(QINGGANG[0],)),
            SeatSpec(equip=(RENWANG[0],)),
            SeatSpec(equip=(VINE[0],)),
        ]
    )

    assert target_is_effective(state, state.card(THUNDER_SLASH[0]), 0, 1) is True, (
        "青釭剑越过仁王盾：黑色雷【杀】照常有效"
    )
    assert target_is_effective(state, state.card(FIRE_SLASH[0]), 0, 2) is True

    with_qinggang = damage_amount(
        state,
        source=0,
        target=2,
        card=state.card(FIRE_SLASH[0]),
        base=1,
        chain=False,
        nature=DamageNature.FIRE,
    )
    bare = _state([SeatSpec(), SeatSpec(), SeatSpec(equip=(VINE[0],))])
    without_qinggang = damage_amount(
        bare,
        source=0,
        target=2,
        card=bare.card(FIRE_SLASH[0]),
        base=1,
        chain=False,
        nature=DamageNature.FIRE,
    )
    assert (with_qinggang, without_qinggang) == (1, 2), (
        "青釭剑越过藤甲 ⇒ 火焰 +1 被压制；对照 1+1=2"
    )


def test_vine_armor_still_ignores_only_normal_slashes():
    """藤甲是 M5-05a 的 **normal-only** 判据：本票的 family 判据不得把它放宽。"""
    state = _state([SeatSpec(), SeatSpec(equip=(VINE[0],))])

    assert target_is_effective(state, state.card(SLASH[0]), 0, 1) is False
    assert target_is_effective(state, state.card(FIRE_SLASH[0]), 0, 1) is True, (
        "火杀是独立牌型（D9），藤甲不挡"
    )
    assert target_is_effective(state, state.card(THUNDER_SLASH[0]), 0, 1) is True, (
        "雷杀同理"
    )


def test_the_engine_keeps_concrete_card_name_branches_out_of_its_own_source():
    """源码纪律：``engine/`` 不出现 ``fire_slash`` / ``thunder_slash`` 具体牌名。"""
    from pathlib import Path

    engine_root = Path(__file__).resolve().parent.parent / "src" / "sgs" / "engine"
    sources = sorted(engine_root.rglob("*.py"))
    assert len(sources) >= 4, "扫描目标可能指错了"

    offenders = [
        f"{path.name}:{lineno}"
        for path in sources
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for name in ("fire_slash", "thunder_slash")
        if name in line
    ]
    assert not offenders, "引擎侧出现了属性牌名：" + "；".join(offenders)


def test_the_card_ref_model_still_has_no_attribute_field():
    """D2 / ADR-003：属性住行为表，``CardRef`` 不加属性字段。"""
    from sgs.state import CardRef

    assert "nature" not in CardRef.model_fields
    assert "attribute" not in CardRef.model_fields
    assert "damage_type" not in CardRef.model_fields
