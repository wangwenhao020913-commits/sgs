"""卡牌事实缓冲 ``Engine.card_events``（chain-death-voices 票 03 / spec §Solution 3）。

口径（票面「验收 1」逐字）：

- 一次**使用**（``Engine.start_use``）或**打出**（``Engine.play_card``）⇒ 恰好一条
  :class:`~sgs.engine.game.CardUseEvent`：``seq`` / ``seat`` / ``card_type`` /
  ``card_name`` / ``gender`` / ``virtual`` 六项齐全；
- ``card_type`` / ``card_name`` 取**产物**（转化 / 纯虚拟是"当成什么牌"，不是来源实体牌）；
- ``gender`` 取**使用者**的武将性别（无武将模式 = ``None``，前端回落 ``male``）；
- **不记**重铸 / 弃置 / 判定 / 摸牌 / 装备置入（``PUT``）——它们不是"使用 / 打出"；
- **不进记录流**：``dump_journal`` 逐字节与没有读者时一致（金录像机械判据）。

两条取数路径（``CODING_STANDARDS.md`` §6「读取方断言走真实产出路径」）：

- 使用链与打出链走**真引擎入口**（``start_use`` / ``play_card``），再用 ``PassPlayer``
  把结算推完——证明这条通道由产品路径真的写过；
- 转化 / 纯虚拟走**真构造器**（``transformed_card`` / ``pure_virtual_card``）：它们就是技能侧
  喂给 ``start_use`` 的那个产物对象，不在这里另造一个虚拟 `CardRef`。
"""

from __future__ import annotations

from sgs.data import Gender, load_content_pack
from sgs.engine import Engine, dump_journal
from sgs.engine.game import CardUseEvent
from sgs.skills.virtual import pure_virtual_card, transformed_card
from sgs.state import FrameKind, MoveReason, Reforge
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")
SLASH = cards_of_type(PACK, "slash")[0]
"""一张实体【杀】：真使用链的产物。"""


def _engine(seats, *, hand=None, seed: int = 11) -> tuple[Engine, object]:
    """人为局面 + 真引擎（``turn_seat=0``，座位 0 是使用者）。"""
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *([hand] if hand is not None else [])),
        turn_seat=0,
    )
    return Engine.from_state(state, seed=seed), state


def _use_chain(engine: Engine) -> Engine:
    """把一次使用推到【杀】的伤害结算结束（没有伤害时推到它自己的帧出栈）。"""
    return run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.DAMAGE_END) or not e.stack.frames,
    )


# ---------------------------------------------------------------------------
# 真使用链：【杀】⇒ 恰好一条，六项齐全
# ---------------------------------------------------------------------------


def test_a_real_slash_use_records_exactly_one_card_fact():
    engine, state = _engine(
        [SeatSpec(hand=(SLASH,), gender=Gender.MALE), SeatSpec(hp=4)],
        hand=SLASH,
    )

    engine.start_use(state.card(SLASH), source=0, targets=(1,))
    _use_chain(engine)

    assert len(engine.card_events) == 1, "一次使用 ⇒ 恰好一条（不逐点、不重复）"
    event = engine.card_events[0]
    assert isinstance(event, CardUseEvent)
    assert set(CardUseEvent.__dataclass_fields__) == {
        "seq",
        "seat",
        "card_type",
        "card_name",
        "gender",
        "virtual",
    }, "六字段的模型就是前端形状的真源"
    assert event.seat == 0, "使用者"
    assert event.card_type == "slash" and event.card_name == "杀"
    assert event.virtual is False, "实体牌 ⇒ 不是转化 / 虚拟产物"
    assert event.gender is Gender.MALE, "性别取使用者自己的武将（前端据它选男 / 女声）"
    assert engine.state.player(1).hp == 3, "规则面照常结算（这条缓冲不替代任何判据）"


def test_the_anchor_is_the_use_frame_record_the_frontend_replays():
    """``seq`` 锚在 ``USE`` 帧那条记录上：前端回放到这一步才出声。"""
    engine, state = _engine([SeatSpec(hand=(SLASH,), gender=Gender.MALE), SeatSpec(hp=4)])

    engine.start_use(state.card(SLASH), source=0, targets=(1,))

    event = engine.card_events[0]
    anchor = [record for record in engine.records if getattr(record, "seq", None) == event.seq]
    assert len(anchor) == 1, "锚点必须落在**本批**的一条记录上（游标才不会重复下发）"
    assert anchor[0].kind == "frame" and anchor[0].op == "push"
    assert anchor[0].frame_kind is FrameKind.USE, (
        f"锚点 = USE 帧的 push 记录，实际：{anchor[0]}"
    )


# ---------------------------------------------------------------------------
# 打出（PLAY 帧）：响应 / 打出各一条
# ---------------------------------------------------------------------------


def test_playing_a_card_records_one_fact_anchored_on_the_play_frame():
    """``play_card`` 是**另一个**入口（【决斗】里打出【杀】走这条）：一次打出一条。"""
    engine, state = _engine([SeatSpec(hand=(SLASH,), gender=Gender.FEMALE), SeatSpec(hp=4)])

    engine.play_card(state.card(SLASH), source=0, timing=Timing.PLAY_DECLARE)

    assert len(engine.card_events) == 1, "打出也是「这张牌被用掉了」：一条"
    event = engine.card_events[0]
    anchor = [record for record in engine.records if getattr(record, "seq", None) == event.seq][0]
    assert anchor.frame_kind is FrameKind.PLAY, (
        f"打出的锚点 = PLAY 帧的 push 记录，实际：{anchor}"
    )
    assert event.seat == 0 and event.card_type == "slash" and event.virtual is False
    assert event.gender is Gender.FEMALE


def test_the_two_entries_are_mutually_exclusive_for_one_action():
    """一次动作只走其中一条：使用不发 PLAY 帧、打出不发 USE 帧，事件因此也只一条。"""
    engine, state = _engine([SeatSpec(hand=(SLASH,), gender=Gender.MALE), SeatSpec(hp=4)])
    engine.start_use(state.card(SLASH), source=0, targets=(1,))

    kinds = [
        record.frame_kind
        for record in engine.records
        if record.kind == "frame" and record.op == "push"
    ]
    assert FrameKind.USE in kinds and FrameKind.PLAY not in kinds
    assert len(engine.card_events) == 1


# ---------------------------------------------------------------------------
# 转化 / 纯虚拟：播**产物**的语音
# ---------------------------------------------------------------------------


def test_a_transformed_card_records_the_product_type_not_the_source_card():
    """【龙胆】闪→杀（也可读作【武圣】红牌→杀）：事件取**产物**牌型、且 ``virtual = True``。

    来源牌型与产物牌型**必须不同**：拿【杀】当来源时，读来源与读产物都得到 ``"slash"``，
    断言分不出来（评审 FIX 2）。这里来源是【闪】、产物是【杀】——读来源就会红。
    """
    dodge = cards_of_type(PACK, "dodge")[0]
    engine, state = _engine(
        [SeatSpec(hand=(dodge,), gender=Gender.MALE), SeatSpec(hp=4)], hand=dodge
    )
    assert state.card(dodge).type == "dodge", "先确认来源实体牌真的是【闪】（与产物不同）"
    product = transformed_card(state, (dodge,), "slash")
    assert product.type == "slash", "产物是【杀】"

    engine.start_use(product, source=0, targets=(1,))

    event = engine.card_events[0]
    assert event.card_type == "slash", "产物牌型（来源实体牌是【闪】⇒ 读来源会得到 dodge）"
    assert event.card_name == "杀"
    assert event.virtual is True, "转化产物 ⇒ virtual（同一条语音，但事实要分辨得出来）"


def test_a_pure_virtual_card_records_its_product_type_too():
    """【八卦阵】视为【闪】：纯虚拟产物同样触发，播的是【闪】。"""
    engine, state = _engine([SeatSpec(gender=Gender.FEMALE), SeatSpec(hp=4)])
    virtual = pure_virtual_card(state, "dodge")

    engine.start_use(virtual, source=0, targets=())

    event = engine.card_events[0]
    assert (event.card_type, event.card_name) == ("dodge", "闪")
    assert event.virtual is True, "纯虚拟 ⇒ 没有来源实体牌，仍是一条使用事实"


# ---------------------------------------------------------------------------
# 不记：重铸 / 弃置 / 判定 / 摸牌 / 装备置入
# ---------------------------------------------------------------------------


def test_a_reforge_is_not_a_use_and_records_nothing():
    """重铸走 ``MOVE`` 帧（``start_reforge``），不是使用 / 打出。"""
    engine, state = _engine([SeatSpec(hand=(SLASH,), gender=Gender.MALE), SeatSpec(hp=4)])

    engine.start_reforge(Reforge(card=state.card(SLASH), answers_seq=0), source=0)
    run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: not e.stack.frames)

    moves = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_move"
    ]
    assert any(record.reason is MoveReason.REFORGE for record in moves), (
        "先确认重铸真的发生了（牌真的移动过）"
    )
    assert engine.card_events == (), "重铸不是使用：一条都不记"


def test_using_an_equipment_records_only_the_use_not_the_put_into_the_slot():
    """装备的**使用**有一条；随之而来的 ``PUT``（进装备槽）不再加一条。"""
    equip = cards_of_type(PACK, "crossbow")[0]
    engine, state = _engine([SeatSpec(hand=(equip,), gender=Gender.MALE), SeatSpec(hp=4)])

    engine.start_use(state.card(equip), source=0, targets=())
    run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: not e.stack.frames)

    moves = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_move"
    ]
    assert any(record.reason is MoveReason.PUT for record in moves), "装备置入真的发生了"
    assert [event.card_type for event in engine.card_events] == ["crossbow"], (
        "只记那一次**使用**（前端听着是「装备牌没有语音」的静默，不是两条）"
    )


def test_drawing_and_discarding_in_a_quiet_turn_records_nothing():
    """摸牌 / 弃置不是使用：一整回合一张牌都没用时，事件缓冲一条都不长。

    弃置**必须真的发生**（评审 FIX 1）：手牌上限 = 体力，故座位 0 以 2 体力、2 张手牌起手，
    摸 2 张后弃牌阶段要真的弃 2 张（``DiscardQuery`` → ``MoveReason.DISCARD``）——空手
    走完回合只会证明摸牌不触发，证明不了弃置。先钉 ``DISCARD`` 真的落进记录流，再钉零事件。

    重铸不塞进这一回合：它有自己的真引擎入口用例
    （``test_a_reforge_is_not_a_use_and_records_nothing``，同样先钉 ``REFORGE`` 再钉零事件）；
    判定同理由 ``test_a_judgement_card_records_nothing`` 钉住。spec 那一行的四种来源因此
    各有**真执行**证据，没有一条靠"没跑到"假装覆盖。
    """
    first = cards_of_type(PACK, "dodge")[0]
    second = cards_of_type(PACK, "peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=2, hp_max=4, hand=(first, second)), SeatSpec()],
        deck=deck_excluding(PACK, first, second),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=11)

    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.TURN_END) and not e.stack.frames,
    )

    reasons = {
        record.reason
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_move"
    }
    assert MoveReason.DRAW in reasons, "先确认摸牌真的发生了"
    assert MoveReason.DISCARD in reasons, (
        "先确认弃置真的发生了（2 体力 / 2 手牌起手、摸 2 张 ⇒ 弃牌阶段弃 2 张）："
        f"{sorted(reason.value for reason in reasons)}"
    )
    assert engine.card_events == (), f"摸牌 / 弃置不是使用 / 打出：{engine.card_events}"


def test_a_judgement_card_records_nothing():
    """判定也不是使用：把【乐不思蜀】放进判定区，回合判定不产生卡牌事实。"""
    indulgence = cards_of_type(PACK, "indulgence")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(judge=(indulgence,)), SeatSpec()],
        deck=deck_excluding(PACK, indulgence),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=5)

    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: any(
            record.kind == "delta"
            and record.delta_type == "card_move"
            and record.reason is MoveReason.JUDGE
            for record in e.records
        ),
    )

    judged = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_move"
        and record.reason is MoveReason.JUDGE
    ]
    assert judged, "先确认判定真的发生了"
    assert engine.card_events == (), "判定不是使用 / 打出"


def test_the_gender_is_none_in_a_hero_less_game():
    """无武将模式 ⇒ ``gender = None``（前端回落 ``male``，引擎不猜）。"""
    engine, state = _engine([SeatSpec(hand=(SLASH,)), SeatSpec(hp=4)])

    engine.start_use(state.card(SLASH), source=0, targets=(1,))

    assert engine.card_events[0].gender is None


# ---------------------------------------------------------------------------
# 不进记录流：金录像逐字节不变
# ---------------------------------------------------------------------------


def test_the_channel_never_touches_the_record_stream():
    """派生通道的硬约束：记录流与没有读者时**逐字节**一致。"""
    engine, state = _engine(
        [SeatSpec(hand=(SLASH,), gender=Gender.MALE), SeatSpec(hp=4)], hand=SLASH
    )
    engine.start_use(state.card(SLASH), source=0, targets=(1,))
    _use_chain(engine)

    assert engine.card_events, "先确认这一局真的记了事实"
    journal = dump_journal(engine.records)
    assert "card_event" not in journal and "card_use" not in journal, (
        "卡牌事实不是记录流的一条记录（进了就会改 47 条金录像的字节）"
    )
    replayed = Engine.from_state(
        build_state(
            pack=PACK,
            seats=[SeatSpec(hand=(SLASH,), gender=Gender.MALE), SeatSpec(hp=4)],
            deck=deck_excluding(PACK, SLASH),
            turn_seat=0,
        ),
        seed=11,
    )
    replayed.start_use(replayed.state.card(SLASH), source=0, targets=(1,))
    _use_chain(replayed)
    assert dump_journal(replayed.records) == journal, (
        "同样的种子与动作 ⇒ 记录流逐字节相同（这条缓冲不消耗随机数）"
    )
