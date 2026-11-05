"""M5-02：属性伤害管线（火杀 / 雷杀）与共享【杀】次数。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流，配合一个只记
「压栈帧载荷」的 :class:`_FrameSpy`——``nature`` / ``chain`` 是帧私有事实，记录流里不落。

军争牌只在 ``standard_ex_160`` 档里，故本模块每个用例都显式选这一档（档位纪律）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sgs.data import Color, color_of_suit, load_content_pack
from sgs.effects import DamageNature, check_count
from sgs.engine import Engine
from sgs.skills import transformed_card
from sgs.state import CardRef, FrameKind, Pass, Phase, QueryKind, UseCard
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_ex_160")
FIRE_SLASH = cards_of_type(PACK, "fire_slash")
THUNDER_SLASH = cards_of_type(PACK, "thunder_slash")
SLASH = cards_of_type(PACK, "slash")
DODGE = cards_of_type(PACK, "dodge")

ENGINE_ROOT = Path(__file__).resolve().parent.parent / "src" / "sgs" / "engine"
"""源码纪律扫描的落点（与 ``tests/test_source_discipline.py`` 同一棵树）。"""


class _FrameSpy(Engine):
    """记下每一次压栈的帧类型与载荷（``nature`` 这类帧私有事实只能这样观测）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pushed: list[tuple[Any, dict[str, Any]]] = []

    def push_frame(self, kind, data, *, timing):  # type: ignore[no-untyped-def]
        frame = super().push_frame(kind, data, timing=timing)
        self.pushed.append((kind, dict(data)))
        return frame


class _PlayOneCard:
    """出牌阶段使用指定 ``card_id`` 的那张牌（目标是 1 号座位）；其余询问最小应答。"""

    def __init__(self, card_id: int, *, slip: bool = False) -> None:
        self.card_id = card_id
        self.slip = slip

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == self.card_id:
                    return action
            return next(
                action for action in query.legal_actions if action.kind == "EndPhase"
            )
        if query.kind == QueryKind.DODGE:
            if not self.slip:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == "dodge":
                        return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == QueryKind.PEACH:
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        return query.legal_actions[0]


def _use_chain_done(engine) -> bool:
    """这张牌的使用链整个跑完（USE 帧已出栈、结算栈空）。"""
    popped_use = any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.USE
        for record in engine.records
    )
    return popped_use and not engine.stack


def _attribute_engine(type_id: str, *, hp_b: int = 4, dodge: bool = False):
    """座位 0 手握一张 ``type_id``、座位 1 空手（或一张【闪】）；跑到这次使用链跑完即停。"""
    card = cards_of_type(PACK, type_id)[0]
    defender_hand = (DODGE[0],) if dodge else ()
    deck = tuple(
        index
        for index in range(len(PACK.deck.cards))
        if index != card and index not in defender_hand
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,)), SeatSpec(hp=hp_b, hand=defender_hand)],
        deck=deck,
        turn_seat=0,
    )
    engine = _FrameSpy.from_state(state, seed=1)
    run_until(
        engine,
        [_PlayOneCard(card), _PlayOneCard(card, slip=not dodge)],
        _use_chain_done,
    )
    return engine, card


def _damage_payloads(engine: _FrameSpy) -> list[dict]:
    return [data for kind, data in engine.pushed if kind is FrameKind.DAMAGE]


def _deck_excluding_cards(*excluded: int) -> tuple[int, ...]:
    kept = frozenset(excluded)
    return tuple(index for index in range(len(PACK.deck.cards)) if index not in kept)


# ---------------------------------------------------------------------------
# 源码纪律：属性住行为表，`engine/` 不认识火 / 雷杀的牌名
# ---------------------------------------------------------------------------


def test_the_engine_does_not_know_the_attribute_slash_card_names():
    """`engine/` 不认识 ``fire_slash`` / ``thunder_slash``：属性声明只住行为表。

    只钉本票新增的两个牌名。``engine/steps.py:_slash_canceled_seam`` 里的 ``"slash"``
    字面量是前票遗留的接缝判据（青龙 / 贯石斧的"杀被抵消时"），不在本票范围。
    """
    sources = sorted(ENGINE_ROOT.rglob("*.py"))
    assert len(sources) >= 4, "扫描目标可能指错了"

    offenders = [
        f"{path.name}:{lineno}"
        for path in sources
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for name in ("fire_slash", "thunder_slash")
        if name in line
    ]

    assert not offenders, "引擎侧出现了属性牌名：" + "；".join(offenders)


# ---------------------------------------------------------------------------
# 属性：火 / 雷 / 普通各自声明，帧的 nature 与来源牌型一致、chain=False
# ---------------------------------------------------------------------------


def test_a_fire_slash_declares_fire_nature_on_the_damage_frame():
    engine, card = _attribute_engine("fire_slash")

    damage = _damage_payloads(engine)
    assert len(damage) == 1, "一次火杀恰好压出一个伤害帧"
    assert damage[0]["nature"] is DamageNature.FIRE
    assert damage[0]["chain"] is False, "源头属性伤害不传导"
    assert damage[0]["channel"].card_id == card, "渠道 = 使用的实体火杀"


def test_a_thunder_slash_declares_thunder_nature_on_the_damage_frame():
    engine, card = _attribute_engine("thunder_slash")

    damage = _damage_payloads(engine)
    assert len(damage) == 1
    assert damage[0]["nature"] is DamageNature.THUNDER
    assert damage[0]["chain"] is False
    assert damage[0]["channel"].card_id == card, "渠道 = 使用的实体雷杀"


def test_a_plain_slash_still_declares_normal_nature_and_no_chain():
    engine, card = _attribute_engine("slash")

    damage = _damage_payloads(engine)
    assert len(damage) == 1
    assert damage[0]["nature"] is DamageNature.NORMAL, "普通【杀】仍是普通伤害（回归）"
    assert damage[0]["chain"] is False
    assert damage[0]["channel"].card_id == card


def test_the_fire_slash_damage_frame_keeps_the_six_elements_from_the_used_card():
    """六要素：``source`` / ``target`` / ``amount`` / ``nature`` / ``channel`` / ``chain``。

    属性只换 ``nature`` 一项；其余五项与普通【杀】逐项同形，且 ``channel`` 就是打出去的
    那张实体火杀（不是牌名、不是产物 id）。
    """
    engine, card = _attribute_engine("fire_slash")

    damage = _damage_payloads(engine)[0]

    assert damage["source"] == 0
    assert damage["target"] == 1
    assert damage["amount"] == 1
    assert damage["nature"] is DamageNature.FIRE
    assert damage["channel"].card_id == card
    assert damage["channel"].type == "fire_slash"
    assert damage["chain"] is False


def test_a_dodge_still_cancels_a_fire_slash_before_effect():
    engine, _card = _attribute_engine("fire_slash", dodge=True)

    assert not _damage_payloads(engine), "【闪】响应照常抵消属性【杀】，不进伤害结算"
    assert engine.state.player(1).hp == 4, "被闪掉的伤害不扣体力"
    assert any(
        record.kind == "query" and record.query.kind == QueryKind.DODGE.value
        for record in engine.records
    ), "属性【杀】照常开【闪】窗口（response_window 未变）"


def test_a_fire_slash_still_drives_near_death_and_death():
    engine, _card = _attribute_engine("fire_slash", hp_b=1)

    pushed = [kind for kind, _data in engine.pushed]
    assert FrameKind.NEAR_DEATH in pushed, "1 体力角色挨火杀仍进濒死"
    assert FrameKind.DEATH in pushed, "无人出【桃】时濒死照常进入死亡"
    hp = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "hp_change"
    ]
    assert [(record.seat, record.before, record.after) for record in hp] == [(1, 1, 0)]


def test_a_fire_slash_dispatches_the_same_damage_hook_timings_as_a_plain_slash():
    engine, _card = _attribute_engine("fire_slash")

    damage_id = next(
        record.frame_id
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind is FrameKind.DAMAGE
    )

    assert [
        record.timing
        for record in engine.records
        if record.kind == "timing" and record.frame_id == damage_id
    ] == [
        Timing.DAMAGE_DEAL,
        Timing.DAMAGE_TAKE,
        Timing.DAMAGE_AFTER_DEAL,
        Timing.DAMAGE_AFTER_TAKE,
        Timing.DAMAGE_END,
    ], "伤害后钩子的派发顺序不随属性改变"


# ---------------------------------------------------------------------------
# 共享【杀】次数：普通 / 火 / 雷共用一个「本回合已用【杀】」计数（M5-01a 留下）
# ---------------------------------------------------------------------------

SLASH_FAMILY = ("slash", "fire_slash", "thunder_slash")


def _slash_cards() -> dict[str, int]:
    """三种【杀】各取一张实体牌（普通取第一张、火 / 雷各取第一张）。"""
    return {
        "slash": SLASH[0],
        "fire_slash": FIRE_SLASH[0],
        "thunder_slash": THUNDER_SLASH[0],
    }


def test_normal_fire_and_thunder_slashes_read_the_same_per_turn_slash_counter():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=_deck_excluding_cards(*_slash_cards().values()),
        turn_seat=0,
        phase=Phase.PLAY,
    )

    state.player(0).card_use_counts["slash"] = 1

    for type_id, card_id in _slash_cards().items():
        assert check_count(state, 0, state.card(card_id)) is True, (
            f"{type_id} 与普通【杀】共用同一个计数：本回合已出过【杀】⇒ 它也不能再出"
        )

    state.player(0).card_use_counts.clear()
    state.player(0).card_use_counts["fire_slash"] = 1
    assert check_count(state, 0, state.card(_slash_cards()["slash"])) is False, (
        "计数键不是火杀自己的牌型：单记 fire_slash 不该挡住普通【杀】"
    )


def test_a_transformed_slash_product_reads_the_shared_slash_counter():
    """转化 / 虚拟【杀】按**产物牌型**计入同一计数；来源的花色 / 点数 / 颜色不参与。"""
    red_source = FIRE_SLASH[0]  # 红桃，普通【杀】的转化源
    black_source = next(
        card_id
        for card_id in SLASH
        if color_of_suit(PACK.deck.cards[card_id].suit) is Color.BLACK
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(red_source, black_source)), SeatSpec()],
        deck=_deck_excluding_cards(red_source, black_source),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    products = [
        transformed_card(state, (red_source,), "slash"),
        transformed_card(state, (black_source,), "slash"),
    ]
    assert [product.type for product in products] == ["slash", "slash"], "产物牌型都是普通【杀】"

    state.player(0).card_use_counts["slash"] = 1
    for product in products:
        assert check_count(state, 0, product) is True, (
            "产物是普通【杀】⇒ 与实体【杀】共用计数；来源的花色 / 颜色不改变这一点"
        )


def test_every_slash_variant_spends_the_same_per_turn_slash_quota():
    """任一【杀】用出去后，普通 / 火 / 雷都不再出现在候选里（第二个【杀】被拒）。"""
    cards = _slash_cards()

    for used_type, used_card in cards.items():
        state = build_state(
            pack=PACK,
            seats=[SeatSpec(hand=tuple(cards.values())), SeatSpec()],
            deck=_deck_excluding_cards(*cards.values()),
            turn_seat=0,
        )
        engine = _FrameSpy.from_state(state, seed=1)
        run_until(
            engine,
            [_PlayOneCard(used_card), _PlayOneCard(used_card, slip=True)],
            _two_play_phase_queries,
        )

        queries = [
            record.query
            for record in engine.records
            if record.kind == "query" and record.query.kind == QueryKind.PLAY_PHASE.value
        ]
        offered = [
            [
                action.card.type
                for action in query.legal_actions
                if isinstance(action, UseCard) and action.card.type in SLASH_FAMILY
            ]
            for query in queries
        ]

        assert len(queries) >= 2, f"{used_type}：这一局至少要问两次出牌阶段"
        assert set(offered[0]) == set(SLASH_FAMILY), "第一问三种【杀】都可出（非空转）"
        assert all(not group for group in offered[1:]), (
            f"{used_type} 用过之后，普通 / 火 / 雷【杀】都不能再出（同一计数），实际：{offered[1:]}"
        )
        assert engine.state.player(0).card_use_counts == {"slash": 1}, (
            "计数落在共享的【杀】键上，而不是各自牌型"
        )


def _two_play_phase_queries(engine) -> bool:
    kinds = [
        record.query.kind for record in engine.records if record.kind == "query"
    ]
    return kinds.count(QueryKind.PLAY_PHASE.value) >= 2


def test_a_slash_product_typed_fire_slash_deals_fire_whatever_its_source():
    """M5-05 朱雀羽扇的产物形态：来源是一张**普通【杀】**，产物牌型 ``fire_slash`` ⇒ 火焰伤害。

    本票不做羽扇（M5-05），但属性按**产物牌型**取，故同一条管线对产物天然成立——这条用例
    用 ``start_use`` 直接把「产物 = fire_slash」喂进既有使用链，钉住机制而不是羽扇技能。
    """
    source = SLASH[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(source,)), SeatSpec()],
        deck=_deck_excluding_cards(source),
        turn_seat=0,
    )
    product = CardRef(
        card_id=-999_999, type="fire_slash", name="火杀", virtual_sources=(source,)
    )
    engine = _FrameSpy.from_state(state, seed=1)
    engine.start_use(product, source=0, targets=(1,))
    run_until(engine, [_PlayOneCard(-1), _PlayOneCard(-1)], _use_chain_done)

    damage = _damage_payloads(engine)
    assert len(damage) == 1
    assert damage[0]["nature"] is DamageNature.FIRE, "来源是普通【杀】，产物牌型决定属性"
    assert damage[0]["chain"] is False
    assert damage[0]["channel"].card_id == -999_999, "渠道 = 产物牌，不是来源实体牌"
    assert engine.state.player(1).hp == 3, "火焰伤害照常结算 1 点"
