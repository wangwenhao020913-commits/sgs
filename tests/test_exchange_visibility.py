"""ai-view 票 12：**交换牌的可见性**——手牌 / 装备区 → 效果区，保持牌面公开性。

用户 2026-11-24 的口径（票面逐字）：

    交换手牌 / 装备区的牌时，保持牌的可见性信息：如果交换的牌本来就是公开的（装备区的牌 /
    手牌区被明置的牌），在效果区以正面朝上展示，否则以背面朝上。

落点是**牌移动（delta）的可见性规则**，不是步骤答案规则（不进 ``STEP_VISIBILITY``）：

1. **持久公开事实** = :attr:`~sgs.data.CardTagType.PUBLIC_FACE` 标签；
   写入口 = :meth:`sgs.engine.Engine.mark_card_face_public`（明置技能另票）；
   寿命 = **进入弃牌堆即清**（交换要穿过 hand → processing → hand，``LEAVE_ZONE`` 会在
   进处理区那一刻清掉，不满足用户要求）。
2. **记录流**（:func:`sgs_web.visibility.cards_visible`）：
   ``(HAND → PROCESSING, LOSE)`` 全部公开 ⇒ 照发，有一张不公开 ⇒ ``cards=[]`` +
   ``card_count``（**不按查看者开洞**）；``(PROCESSING → HAND, PUT)`` 读同一份
   "进处理区那刻是否公开"（:func:`sgs_web.projection.processing_entry_faces`），非公开的牌
   只给**收到它的本人**。
3. **视图 / 舞台**：``Engine.processing_concealed`` → ``PlayerView.processing_concealed``
   （``exclude=True``，不进记录流）→ ``display._processing_display`` 不画牌面。

判据全部走外部缝：真引擎的 ``record_json`` / ``visible_record`` / 真
``GameRegistry.view``（网页下发的生产路径）/ ``visible_snapshot`` / 终局牌位。
不 mock 可见性函数、不旁路候选。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src", REPO_ROOT / "tests"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.agents import heuristic
from sgs.data import CardTagType, hero_skill_id, load_content_pack
from sgs.engine import CardMoveRecord, Engine, record_json
from sgs.state import (
    ActivateSkill,
    EndPhase,
    MoveReason,
    Pass,
    Phase,
    QueryKind,
    SelectCards,
    SelectOption,
    Zone,
    ZoneRef,
    project,
)
from sgs_web.projection import processing_entry_faces, visible_record, visible_snapshot
from sgs_web.serve import GameRegistry
from sgs_web.session import Session

from support import SeatSpec, build_state, cards_of_type, deck_excluding

PACK = load_content_pack(deck_profile="standard_108")
LU_SU = "lu_su_mobile"
HAOSHI = hero_skill_id(LU_SU, "haoshi")
DIMENG = hero_skill_id(LU_SU, "dimeng")


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _card(type_id: str) -> int:
    return _cards(type_id)[0]


def _minimal(query):  # noqa: ANN001 - 与 Participant 协议一致
    """最小合法应答（只用于非交换角色的过路询问）。"""
    if query.kind == QueryKind.PLAY_PHASE:
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    if query.kind == QueryKind.SELECT_CARDS:
        return next(a for a in query.legal_actions if isinstance(a, SelectCards))
    if query.kind == QueryKind.SELECT_OPTION:
        return next(a for a in query.legal_actions if isinstance(a, SelectOption))
    return query.legal_actions[0]


class _LuSu:
    """座位 0 的脚本：两条技能都放弃；出牌阶段提交给定的【缔盟】两名目标。"""

    def __init__(self, pair: tuple[int, int]) -> None:
        self.pair = pair

    def respond(self, query):  # noqa: ANN001 - Participant 协议
        if query.kind == QueryKind.SKILL_ACTIVATION and str(query.skill_id) in (
            HAOSHI,
            DIMENG,
        ):
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == QueryKind.PLAY_PHASE and self.pair is not None:
            first, second = self.pair
            self.pair = None
            return ActivateSkill(
                skill_id=DIMENG,
                picks=(),
                choices=(f"seat:{first}", f"seat:{second}"),
                answers_seq=query.seq,
            )
        return _minimal(query)


class _Decline:
    def respond(self, query):  # noqa: ANN001
        return _minimal(query)


def _play_idle(engine: Engine, actor: int = 0) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
        and engine.pending.actor == actor
    )


def _drive(engine: Engine, players, stop) -> None:  # noqa: ANN001
    for _ in range(2000):
        if engine.finished or stop(engine):
            return
        if engine.pending is None:
            engine._step()
        else:
            engine.submit(players[engine.pending.actor].respond(engine.pending))
    raise AssertionError("推进上限内没有到达停止条件")


def _dimeng_engine(
    *, hand_a: tuple[int, ...], hand_b: tuple[int, ...], prepare=None
):
    """5 座构造局面：座位 0 = 鲁肃，座位 1/2 交换手牌，座位 3 = 人类（第三方）。

    座位 0 从出牌阶段起步（``phase=PLAY``），故交换只由【缔盟】的 effect 搬牌。
    ``prepare(engine, state)`` 在提交【缔盟】**之前**跑（明置这类事实必须先在记录流里）。
    """
    mine = (_card("slash"), _card("dodge"))
    seats = [
        SeatSpec(hero=LU_SU, hp=3, hp_max=3, hand=mine),
        SeatSpec(hand=hand_a),
        SeatSpec(hand=hand_b),
        # 座位 3 = 人类（第三方）；座位 4 = 另一名旁观者（票面验收是 **5p** 局）。
        SeatSpec(),
        SeatSpec(),
    ]
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *mine, *hand_a, *hand_b),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    players = [_LuSu((1, 2)), _Decline(), _Decline(), _Decline()]
    _drive(engine, players, _play_idle)
    if prepare is not None:
        prepare(engine, state)
    query = engine.pending
    assert query is not None
    engine.submit(
        ActivateSkill(
            skill_id=DIMENG,
            picks=(),
            choices=("seat:1", "seat:2"),
            answers_seq=query.seq,
        )
    )
    _drive(engine, players, _play_idle)
    return engine, state, mine


def _session_for(engine: Engine, *, human_seat: int) -> Session:
    """把构造出来的引擎包进真会话（``GameRegistry.view`` 需要真会话的游标 / 缓存）。"""
    session = Session(
        seed=0,
        player_count=5,
        ai="random",
        human_seat=human_seat,
        hero_pool=None,
        timeout_seconds=None,
        pack=PACK,
    )
    session.engine = engine
    return session


def _view(session: Session) -> dict:
    """一次真 ``GameRegistry.view`` 载荷（生产下发路径；空注册表，不落盘）。"""
    return GameRegistry(runs_dir="/tmp/unused").view(session, after=-1, logs_after=0)


def _exchange_moves(engine: Engine) -> tuple[list, list]:
    first: list = []
    second: list = []
    for record in engine.records:
        if getattr(record, "delta_type", None) != "card_move":
            continue
        from_zone = getattr(record, "from_zone", None)
        to_zone = getattr(record, "to_zone", None)
        if from_zone is None or to_zone is None:
            continue
        if (
            from_zone.zone is Zone.HAND
            and to_zone.zone is Zone.PROCESSING
            and record.reason is MoveReason.LOSE
        ):
            first.append(record)
        elif (
            from_zone.zone is Zone.PROCESSING
            and to_zone.zone is Zone.HAND
            and record.reason is MoveReason.PUT
        ):
            second.append(record)
    return first, second


def _faces_absent(payload: dict, cards) -> None:  # noqa: ANN001
    """脱敏形态：``cards`` 空 + ``card_count``，下发字节里没有牌面字段。"""
    assert payload["cards"] == [], f"交换的牌面泄露：{payload}"
    assert payload["card_count"] == len(cards), (
        f"脱敏后必须给出张数（应有 {len(cards)} 张）：{payload}"
    )
    text = json.dumps(payload, ensure_ascii=False)
    for key in ("card_id", '"name"', '"suit"', '"rank"'):
        assert key not in text, f"脱敏记录仍带牌面字段 {key}：{payload}"


# ---------------------------------------------------------------------------
# §1 记录流：第三方视角看不到交换的手牌牌面（票面验收第 1 条）
# ---------------------------------------------------------------------------


def test_a_third_party_sees_only_card_counts_while_the_hands_go_through_processing() -> None:
    hand_a = (_card("ex_nihilo"),)
    hand_b = (_card("duel"), _card("peach"))
    engine, _state, _mine = _dimeng_engine(hand_a=hand_a, hand_b=hand_b)
    first, second = _exchange_moves(engine)
    assert len(first) == 2 and len(second) == 2, "交换 = 两次扣置 + 两次回手"

    # 引擎记录流仍是全信息（引擎 / 回放 / 裁判）。
    for record in first + second:
        raw = json.loads(record_json(record))
        assert raw["cards"], "引擎记录流不能被裁：牌面 / 花色 / 点数都还在"
        assert {card["card_id"] for card in raw["cards"]}

    payload = _view(_session_for(engine, human_seat=3))
    shown = {
        record["seq"]: record
        for record in payload["records"]
        if "seq" in record
    }
    for record in first + second:
        _faces_absent(shown[record.seq], record.cards)

    # 两人的手牌数正确变化（人类看到的是**数量**，不是牌面）。
    players = {player["seat"]: player for player in payload["snapshot"]["players"]}
    assert players[1]["hand_count"] == len(hand_b)
    assert players[2]["hand_count"] == len(hand_a)
    # 全知通道（内部 / 回放）不裁：同一批记录带牌面。
    for record in first + second:
        assert visible_record(record, viewer=None)["cards"], "全知通道照发牌面"


def test_the_giver_does_not_see_their_own_exchanged_card_in_the_effect_area() -> None:
    """票面「不按查看者身份开洞」：扣置那一腿对**本人也**只给张数；回手腿给收到的人。"""
    hand_a = (_card("ex_nihilo"),)
    hand_b = (_card("duel"), _card("peach"))
    engine, _state, _mine = _dimeng_engine(hand_a=hand_a, hand_b=hand_b)
    first, second = _exchange_moves(engine)
    payload = _view(_session_for(engine, human_seat=1))
    shown = {
        record["seq"]: record
        for record in payload["records"]
        if "seq" in record
    }

    for record in first:
        _faces_absent(shown[record.seq], record.cards)

    # 回手腿按"收到它的本人"分档：座位 1 收到的照发；座位 2 收到的对座位 1 只给张数。
    received = [record for record in second if record.to_zone.seat == 1][0]
    foreign = [record for record in second if record.to_zone.seat == 2][0]
    assert [card["card_id"] for card in shown[received.seq]["cards"]] == [
        card.card_id for card in received.cards
    ]
    _faces_absent(shown[foreign.seq], foreign.cards)


# ---------------------------------------------------------------------------
# §2 明置的手牌：持久事实 + 交换前后都正面
# ---------------------------------------------------------------------------


def _mark_public(engine: Engine, state, card_id: int, *, owner: int) -> None:
    """交换前把一张手牌标成明置：前置检查 + 生产写入口（落 tag_add）。"""
    assert not state.has_card_tag(card_id, CardTagType.PUBLIC_FACE), "前置：默认普通手牌"
    assert engine.mark_card_face_public(card_id, owner=owner) is True
    assert state.has_card_tag(card_id, CardTagType.PUBLIC_FACE) is True


def test_a_public_face_hand_card_stays_face_up_through_the_exchange() -> None:
    revealed = _card("duel")
    hand_a = (revealed,)
    hand_b = (_card("peach"),)
    engine, state, _mine = _dimeng_engine(
        hand_a=hand_a,
        hand_b=hand_b,
        # 生产写入口（不是直接改 state.card_tags）；在交换**之前**落——明置是
        # 交换前的持久公开事实，tag_add 也因此在记录流里早于交换。
        prepare=lambda e, st: _mark_public(e, st, revealed, owner=1),
    )
    assert state.has_card_tag(revealed, CardTagType.PUBLIC_FACE) is True
    tag_records = [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) in ("tag_add", "tag_remove")
        and record.tag_type is CardTagType.PUBLIC_FACE
    ]
    assert [record.delta_type for record in tag_records] == ["tag_add"]

    first, second = _exchange_moves(engine)
    payload = _view(_session_for(engine, human_seat=3))
    shown = {
        record["seq"]: record
        for record in payload["records"]
        if "seq" in record
    }

    mine_first = [record for record in first if record.from_zone.seat == 1][0]
    mine_second = [record for record in second if record.to_zone.seat == 2][0]
    other_first = [record for record in first if record.from_zone.seat == 2][0]
    for record in (mine_first, mine_second):
        assert [card["card_id"] for card in shown[record.seq]["cards"]] == [revealed], (
            f"明置牌在效果区要正面朝上：{shown[record.seq]}"
        )
    _faces_absent(shown[other_first.seq], other_first.cards)

    # 寿命：交换后仍明置（直到进入弃牌堆）。
    assert state.player(2).hand == [revealed]
    assert state.has_card_tag(revealed, CardTagType.PUBLIC_FACE) is True, (
        "交换经过处理区不能清掉明置事实"
    )
    engine.move_in_frame(
        None,
        state.refs((revealed,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=2,
    )
    assert state.has_card_tag(revealed, CardTagType.PUBLIC_FACE) is False, (
        "进入弃牌堆即清（CardTagClear.ENTER_DISCARD）"
    )


def test_the_event_stream_names_the_public_card_and_counts_the_hidden_one() -> None:
    """事件流（叙事）与记录流同一份判据：明置牌写牌名，普通手牌只写张数。"""
    revealed = _card("duel")
    hand_a = (revealed,)
    hand_b = (_card("peach"),)
    engine, _state, _mine = _dimeng_engine(
        hand_a=hand_a,
        hand_b=hand_b,
        prepare=lambda e, _st: e.mark_card_face_public(revealed, owner=1),
    )
    _first, second = _exchange_moves(engine)
    received = [record for record in second if record.to_zone.seat == 2][0]
    hidden = [record for record in second if record.to_zone.seat == 1][0]

    payload = _view(_session_for(engine, human_seat=3))
    narrative = {entry["seq"]: entry["text"] for entry in payload["narrative"]}
    assert "【决斗】" in narrative[received.seq], (
        f"明置牌的回手腿要在事件流里写牌名：{narrative.get(received.seq)}"
    )
    assert "【桃】" not in narrative[hidden.seq] and "1 张牌" in narrative[hidden.seq], (
        f"非公开手牌的回手腿只给张数：{narrative.get(hidden.seq)}"
    )


# ---------------------------------------------------------------------------
# §3 装备区公开：规则按形状判，不认技能 id
# ---------------------------------------------------------------------------


def _weapon_card() -> int:
    for card_id, card in enumerate(PACK.deck.cards):
        if card.type == "qinggang_sword":
            return card_id
    raise AssertionError("108 档里没有【青釭剑】")


def _two_seat_engine(*, hand=(), equip=()) -> tuple[Engine, object]:
    seats = [SeatSpec(hand=tuple(hand), equip=tuple(equip)), SeatSpec()]
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *hand, *equip),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    return Engine.from_state(state, seed=7), state


def test_an_equipment_card_goes_through_processing_face_up() -> None:
    weapon = _weapon_card()
    engine, state = _two_seat_engine(hand=(_card("peach"),), equip=(weapon,))
    engine.move_in_frame(
        None,
        state.refs((weapon,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    record = engine.records[-1]
    assert record.delta_type == "card_move" and record.to_zone.zone is Zone.PROCESSING
    payload = visible_record(
        record, viewer=3, state=state, exchange_faces=processing_entry_faces(engine.records, state)
    )
    assert [card["card_id"] for card in payload["cards"]] == [weapon], (
        "装备区的牌是公开事实：经处理区时正面朝上"
    )
    assert engine.processing_concealed == frozenset(), (
        "规则不认技能 id：equip → processing 这一形状按公开处理"
    )


def test_the_exchange_rule_is_a_move_shape_not_a_skill_id() -> None:
    """同一批合成记录（不带任何技能归属）走同一份判据——没有 ``dimeng`` 特例。"""
    hidden = _card("peach")
    _engine, state = _two_seat_engine(hand=(hidden,))
    card = state.card(hidden)
    record = CardMoveRecord(
        seq=501,
        frame_id=0,
        depth=0,
        cards=(card,),
        from_zone=ZoneRef(Zone.HAND, 1),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=1,
    )
    assert visible_record(record, viewer=3)["cards"] == [], "非明置手牌 → 背面（只给张数）"
    assert visible_record(record, viewer=None)["cards"], "全知通道不裁"
    back = CardMoveRecord(
        seq=502,
        frame_id=0,
        depth=0,
        cards=(card,),
        from_zone=ZoneRef(Zone.PROCESSING),
        to_zone=ZoneRef(Zone.HAND, 2),
        reason=MoveReason.PUT,
        owner=2,
    )
    faces = {hidden: False}
    assert visible_record(back, viewer=3, exchange_faces=faces)["cards"] == []
    assert visible_record(back, viewer=2, exchange_faces=faces)["cards"], (
        "收到它的本人照发（既有手牌可见性）"
    )
    # 五谷的"牌堆亮出 → 取走"不是交换：没有 exchange_faces 时落回区域判据。
    assert visible_record(back, viewer=3)["cards"], "公开亮出的牌不能被误掩"
    assert visible_record(back, viewer=3, exchange_faces={hidden: True})["cards"]
    # 非公开路径的落点是 equip / judge 时照旧按公开区判（装备公开那一档）。
    equip_move = CardMoveRecord(
        seq=503,
        frame_id=0,
        depth=0,
        cards=(card,),
        from_zone=ZoneRef(Zone.EQUIP, 1),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=1,
    )
    assert visible_record(equip_move, viewer=3)["cards"], "装备区的牌永远公开（不看技能 id）"


# ---------------------------------------------------------------------------
# §4 视图：引擎的"处理区扣置牌"集合 + PlayerView 字段（exclude）+ 舞台
# ---------------------------------------------------------------------------


def test_the_engine_tracks_which_processing_cards_have_hidden_faces() -> None:
    hidden = _card("peach")
    public = _card("duel")
    engine, state = _two_seat_engine(hand=(hidden, public))
    engine.mark_card_face_public(public, owner=0)

    engine.move_in_frame(
        None,
        state.refs((hidden, public)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    assert engine.processing_concealed == frozenset({hidden}), (
        "同一批里明置的那张不扣置、普通手牌扣置"
    )
    view = project(state, 1, concealed_processing=engine.processing_concealed)
    assert view.processing_concealed == (hidden,), "处理区的牌面清单按同一份事实"
    assert "processing_concealed" not in view.model_dump(mode="json"), (
        "exclude=True：不进记录流 / 询问载荷"
    )
    assert [card.card_id for card in view.processing] == [hidden, public], (
        "processing 本身仍是全量（区域事实 / AI 读点 / 守恒量不动）"
    )

    # 明置发生在处理区里 ⇒ 即刻从扣置集合移出。
    engine.mark_card_face_public(hidden, owner=0)
    assert engine.processing_concealed == frozenset()


def test_leaving_processing_clears_the_concealed_fact() -> None:
    hidden = _card("peach")
    engine, state = _two_seat_engine(hand=(hidden,))
    engine.move_in_frame(
        None,
        state.refs((hidden,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    assert engine.processing_concealed == frozenset({hidden})
    engine.move_in_frame(
        None,
        state.refs((hidden,)),
        to_zone=ZoneRef(Zone.HAND, 1),
        reason=MoveReason.PUT,
        owner=1,
    )
    assert engine.processing_concealed == frozenset(), "离开处理区即不在集合里"


def test_a_use_move_into_processing_stays_public() -> None:
    """形状判据：``USE``（使用）不是扣置，进处理区仍是公开事实。"""
    played = _card("peach")
    engine, state = _two_seat_engine(hand=(played,))
    engine.move_in_frame(
        None,
        state.refs((played,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.USE,
        owner=0,
    )
    assert engine.processing_concealed == frozenset()
    assert state.processing == [played]


def test_processing_entry_faces_reads_entry_shapes_and_the_persistent_fact() -> None:
    hidden = _card("peach")
    revealed = _card("duel")
    engine, state = _two_seat_engine(hand=(hidden, revealed))
    engine.mark_card_face_public(revealed, owner=0)
    engine.move_in_frame(
        None,
        state.refs((hidden,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    engine.move_in_frame(
        None,
        state.refs((revealed,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    deck_card = state.deck[0]
    engine.move_in_frame(
        None,
        state.refs((deck_card,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.PUT,
        owner=0,
    )
    faces = processing_entry_faces(engine.records, state)
    assert faces[hidden] is False, "普通手牌扣置 → 不公开"
    assert faces[revealed] is True, "明置事实 → 公开"
    assert faces[deck_card] is True, "牌堆亮出 → 公开（五谷那一形状不被误掩）"


def test_the_stage_does_not_draw_a_concealed_processing_card() -> None:
    hidden = _card("peach")
    engine, state = _two_seat_engine(hand=(hidden,))
    engine.move_in_frame(
        None,
        state.refs((hidden,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    session = SimpleNamespace(engine=engine, human_seat=1, pack=PACK)
    snapshot = visible_snapshot(session)
    assert snapshot["processing"] == [], "扣置进处理区的牌：舞台连牌面都不画"
    assert state.processing == [hidden], "引擎状态不变（只裁下发）"

    engine.mark_card_face_public(hidden, owner=0)
    snapshot = visible_snapshot(session)
    assert [card["card_id"] for card in snapshot["processing"]] == [hidden], (
        "明置之后舞台按正面画"
    )


def test_the_ai_read_point_does_not_read_the_new_field() -> None:
    """票 12 不改 AI 打分：``processing_concealed`` 登记为不读，``processing`` 仍是读。"""
    read = heuristic._AI_READ_POINTS["PlayerView"]
    assert read["processing"][0] is True, "处理区仍是全量读点（本票不动）"
    assert read["processing_concealed"][0] is False, (
        "把扣置牌从定价 / 记牌里摘出去是另票；本票只给展示层"
    )
    assert read["processing_concealed"][1].strip()


# ---------------------------------------------------------------------------
# §5 回放：同一局重放两次，处理区 / 记录视图逐字节一致
# ---------------------------------------------------------------------------


def _payload_bytes(engine: Engine, *, human_seat: int) -> str:
    payload = _view(_session_for(engine, human_seat=human_seat))
    return json.dumps(
        {
            "processing": payload["snapshot"]["processing"],
            "records": payload["records"],
            "narrative": payload["narrative"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def test_replaying_the_same_exchange_twice_is_byte_identical() -> None:
    hand_a = (_card("ex_nihilo"),)
    hand_b = (_card("duel"), _card("peach"))
    engine, _state, _mine = _dimeng_engine(hand_a=hand_a, hand_b=hand_b)
    first = _payload_bytes(engine, human_seat=3)
    second = _payload_bytes(engine, human_seat=3)
    assert first == second, "纯函数：同一条记录流两次下发逐字节一致"

    # 从同一份构造局面重跑一遍（不是复用同一个引擎）：交换的落点 / 可见性一致。
    replay, _state2, _mine2 = _dimeng_engine(hand_a=hand_a, hand_b=hand_b)
    assert _payload_bytes(replay, human_seat=3) == first, (
        "同一构造局面重放的下发视图逐字节一致"
    )


def test_a_mixed_hand_keeps_the_public_card_face_and_counts_the_rest() -> None:
    """一手牌里混着明置与普通手牌：公开的那张照发，其余按张数（逐牌，不整批扣置）。

    这是用户口径的逐牌形态：同一批移动里"明置的正面、普通的手牌背面"。
    下发形状 = ``cards``（可见的那几张）+ ``card_count``（其余张数）；前端
    ``liveMoveCount`` 把两格相加（隐藏档仍然只有 ``card_count``）。
    """
    revealed = _card("duel")
    normal = _cards("slash")[1]  # 与座位 0 的手牌不重（构造局面要求一张牌只在一个区）
    hand_a = (revealed, normal)
    hand_b = (_card("peach"),)
    engine, _state, _mine = _dimeng_engine(
        hand_a=hand_a,
        hand_b=hand_b,
        prepare=lambda e, st: _mark_public(e, st, revealed, owner=1),
    )
    first, second = _exchange_moves(engine)
    payload = _view(_session_for(engine, human_seat=3))
    shown = {record["seq"]: record for record in payload["records"] if "seq" in record}

    mine_first = [record for record in first if record.from_zone.seat == 1][0]
    entry = shown[mine_first.seq]
    assert [card["card_id"] for card in entry["cards"]] == [revealed], (
        f"明置的那张要正面：{entry}"
    )
    assert entry["card_count"] == 1, f"普通手牌只给张数：{entry}"
    text = json.dumps(entry, ensure_ascii=False)
    assert f'"card_id": {normal}' not in text, f"普通手牌的牌面仍在载荷里：{entry}"

    mine_second = [record for record in second if record.to_zone.seat == 2][0]
    entry = shown[mine_second.seq]
    assert [card["card_id"] for card in entry["cards"]] == [revealed], (
        f"回手腿的明置牌同样要正面：{entry}"
    )
    assert entry["card_count"] == 1


def test_a_one_shot_reveal_is_not_the_persistent_public_fact() -> None:
    """【展示】/【火攻】亮牌是一**次性**事件，不是明置事实：交换时仍按背面（票面口径）。

    这正是本票与 ``reveal_card`` 的边界：持久事实 = ``PUBLIC_FACE`` 标签；记录流里的
    ``card_reveal`` 只让那一帧的牌面公开。引擎的 ``processing_concealed`` 与下发表
    （``processing_entry_faces``）因此对同一形状给同一个答案。
    """
    card = _card("peach")
    engine, state = _two_seat_engine(hand=(card,))
    engine.reveal_card(None, 0, state.card(card))
    engine.move_in_frame(
        None,
        state.refs((card,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    assert not state.has_card_tag(card, CardTagType.PUBLIC_FACE), "亮牌不写持久事实"
    assert engine.processing_concealed == frozenset({card})
    faces = processing_entry_faces(engine.records, state)
    assert faces[card] is False, "一次性亮牌不把交换前的牌面算成公开"
    record = engine.records[-1]
    payload = visible_record(record, viewer=3, state=state, exchange_faces=faces)
    assert payload["cards"] == [] and payload["card_count"] == 1


def test_the_new_view_field_never_reaches_the_record_stream() -> None:
    """``PlayerView.processing_concealed`` 是 ``exclude=True`` 派生面：记录流逐字节不变。

    判据走**真实产出路径**：引擎的 canonical journal（``Engine.journal()``）与询问的
    ``model_dump`` 里都不许出现这个键；同一局的下发视图里它却真的有值（不是空转）。
    """
    engine, state, _mine = _dimeng_engine(
        hand_a=(_card("ex_nihilo"),), hand_b=(_card("duel"),)
    )
    assert "processing_concealed" not in engine.journal(), (
        "exclude=True 的派生面不许进记录流（47 条金录像 / 60 条基线的字节纪律）"
    )

    hidden = _card("peach")
    engine2, state2 = _two_seat_engine(hand=(hidden,))
    engine2.move_in_frame(
        None,
        state2.refs((hidden,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    view = project(state2, 1, concealed_processing=engine2.processing_concealed)
    assert view.processing_concealed == (hidden,), "对照：字段真的有值（判据不是空转）"
    assert "processing_concealed" not in view.model_dump_json()
    assert "processing_concealed" not in engine2.journal()


def test_a_card_marked_public_while_in_processing_is_face_up_everywhere() -> None:
    """处理区里被标成明置：引擎集合 / 下发表 / 舞台从同一刻起都按公开（票 12 的一致性）。

    这条钉住评审指出的那一档：引擎 ``processing_concealed``、``processing_entry_faces``
    与 ``visible_snapshot`` 不能一个说背面、另一个说正面。
    """
    hidden = _card("peach")
    engine, state = _two_seat_engine(hand=(hidden,))
    engine.move_in_frame(
        None,
        state.refs((hidden,)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=0,
    )
    assert engine.processing_concealed == frozenset({hidden})
    engine.mark_card_face_public(hidden, owner=0)
    assert engine.processing_concealed == frozenset(), "引擎集合：明置后不再扣置"
    faces = processing_entry_faces(engine.records, state)
    assert faces[hidden] is True, "下发表：处理区里被标成明置 ⇒ 回手腿照发"
    session = SimpleNamespace(engine=engine, human_seat=1, pack=PACK)
    snapshot = visible_snapshot(session)
    assert [card["card_id"] for card in snapshot["processing"]] == [hidden], (
        "舞台：明置后按正面画"
    )

    engine.move_in_frame(
        None,
        state.refs((hidden,)),
        to_zone=ZoneRef(Zone.HAND, 1),
        reason=MoveReason.PUT,
        owner=1,
    )
    record = engine.records[-1]
    payload = visible_record(record, viewer=2, state=state, exchange_faces=faces)
    assert [card["card_id"] for card in payload["cards"]] == [hidden], (
        "记录流：回手腿对第三方也照发（此刻牌面已公开）"
    )
