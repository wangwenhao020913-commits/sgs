"""卡牌事实的接入层下发（chain-death-voices 票 03 / spec §Solution 3）。

口径（票面「验收 2」逐字 + spec §硬约束）：

- 会话载荷多一个 ``card_events`` 键，形状
  ``{seq, seat, card_type, card_name, gender, virtual}``，与 ``damage_events`` **同一套游标语义**；
- ``after`` 等于某条的 ``seq`` ⇒ 再取为空（游标语义，轮询不重复出声）；
- 键在任何使用发生前就存在（空列表，前端不用判 ``undefined``）；
- 静态录像（没有 ``Engine.card_events``）⇒ **空列表、不抛错**；
- 事件**不出现在 ``records`` 里**（通道与记录流分离，金录像逐字节不变）；
- ``card_type`` / ``card_name`` 是**产物**（转化 / 纯虚拟 = "当成什么牌"），``gender`` 取
  使用者的武将性别（无武将模式是 ``null``，前端回落 ``male``）。

两条取数路径（``CODING_STANDARDS.md`` §6）：主用例走**真会话**（``GameRegistry.create`` +
``view``，AI 座位真的用了一张牌），其余走真引擎 / 静态录像喂进同一个投影函数。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from sgs.data import Gender, load_content_pack  # noqa: E402
from sgs.engine import Engine, dump_journal  # noqa: E402
from sgs.engine.game import CardUseEvent  # noqa: E402
from sgs.skills.virtual import pure_virtual_card, transformed_card  # noqa: E402
from sgs.state import EndPhase, Pass  # noqa: E402
from sgs_web.projection import card_events_after  # noqa: E402
from sgs_web.serve import GameRegistry  # noqa: E402

from support import SeatSpec, build_state, cards_of_type, deck_excluding  # noqa: E402

PACK = load_content_pack(deck_profile="standard_108")
SLASH = cards_of_type(PACK, "slash")[0]

ANSWER_SEED = 10
"""2 人会话里 AI 座位**第 2 次**应答后就使用一张【杀】的种子（见 ``_session_with_card_use``）。

固定种子 + 固定应答规则 = 确定的记录流；本文件只借它拿一次**真实**使用，不模拟具体牌局。
"""

CARD_EVENT_FIELDS = {"seq", "seat", "card_type", "card_name", "gender", "virtual"}


def _answer(query):
    """最小合法应答：能结束阶段就结束阶段，否则放弃（与 ``support.PassPlayer`` 同规则）。"""
    if query.kind == "PLAY_PHASE":
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def _session_with_card_use(seed: int = ANSWER_SEED, *, limit: int = 200):
    """推进真会话直到引擎记下**第一条**卡牌事实（人类座位全程最小应答：不出牌）。"""
    registry = GameRegistry(runs_dir="/tmp/unused")
    session = registry.create(
        {"player_count": 2, "ai": "random", "seed": seed, "timeout_seconds": None}
    )
    for _ in range(limit):
        if session.engine.card_events:
            return registry, session
        if session.pending is None:
            session.drive()
            if session.finished:
                break
            continue
        session.submit_answer(_answer(session.pending))
        if session.finished:
            break
    raise AssertionError(f"种子 {seed} 在 {limit} 步内没有用出牌——换种子或改应答规则")


# ---------------------------------------------------------------------------
# 真会话：载荷多一个 card_events 键
# ---------------------------------------------------------------------------


def test_a_real_session_payload_carries_the_card_fact_with_all_six_fields():
    registry, session = _session_with_card_use()

    body = registry.view(session, after=-1, logs_after=0)

    assert "card_events" in body, "会话载荷必须有这个键（前端按它排队出声）"
    events = body["card_events"]
    assert len(events) == 1, f"这一次使用恰好下发一条，实际：{events}"
    event = events[0]
    assert set(event) == CARD_EVENT_FIELDS, (
        f"形状必须稳定（前端按它查映射表）：{event}"
    )
    engine_event = session.engine.card_events[0]
    assert event["seq"] == engine_event.seq, "锚点 = 引擎记下的那条记录的序号"
    assert event["seat"] == engine_event.seat, "使用者原样下发（前端据它取性别）"
    assert event["card_type"] == engine_event.card_type == "slash"
    assert event["card_name"] == engine_event.card_name == "杀"
    assert event["virtual"] is False, "实体牌 ⇒ false（布尔原样下发，不是 'false' 字符串）"
    assert event["gender"] == session.engine.state.player(event["seat"]).gender.value, (
        "男声 / 女声由这一格决定（取座位的性别事实，不写死——池每加一名武将都会重排 RNG）"
    )
    assert 0 <= event["seat"] < 2, "座位是本局真实座位"


def test_the_cursor_is_the_same_one_as_the_records_batch():
    """``after`` 游标语义：取过一条之后，用它自己的 ``seq`` 再取就是空。"""
    registry, session = _session_with_card_use()
    first = registry.view(session, after=-1, logs_after=0)
    event = first["card_events"][0]

    again = registry.view(session, after=event["seq"], logs_after=0)
    assert again["card_events"] == [], "同一条不允许重复下发（轮询不重放出第二声）"

    # 同一个游标走两次也还是空：卡牌事实是**派生通道**，不进记录流、不会改变游标。
    idle = registry.view(session, after=event["seq"], logs_after=0)
    assert idle["card_events"] == []
    assert idle["cursor"] == again["cursor"], "派生通道不推游标"


def test_the_key_is_present_before_any_card_is_used():
    """开局那一批没有卡牌事实 ⇒ 键仍在，值是空列表（前端不用判 ``undefined``）。"""
    registry = GameRegistry(runs_dir="/tmp/unused")
    session = registry.create(
        {"player_count": 2, "ai": "random", "seed": ANSWER_SEED, "timeout_seconds": None}
    )

    body = registry.view(session, after=-1, logs_after=0)

    assert body["card_events"] == []
    assert not session.engine.card_events, "先确认这一批里真的还没有使用"


def test_the_card_channel_does_not_change_the_record_stream():
    """派生通道的硬约束：它不往记录流里加一条，金录像 / 存档形状因此一字不变。"""
    registry, session = _session_with_card_use()

    body = registry.view(session, after=-1, logs_after=0)

    assert body["card_events"], "先确认这一批里真的有卡牌事实"
    for forbidden in ("card_use", "card_event"):
        assert all(record.get("kind") != forbidden for record in body["records"]), (
            "卡牌事实不是记录流的一条记录（进了就会改 47 条金录像的字节）"
        )
    lines = dump_journal(session.engine.records).splitlines()
    assert lines and all(json.loads(line) for line in lines), "记录流仍是可解析的 canonical JSONL"


# ---------------------------------------------------------------------------
# 投影函数本身：产物 / 游标 / 上界 / 座位 / 兜底
# ---------------------------------------------------------------------------


def _engine_with_use(*, virtual: str | None = None, gender: Gender | None = Gender.MALE) -> Engine:
    """真引擎 + 一次真 ``start_use``（真牌 / 转化 / 纯虚拟三种产物）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), gender=gender), SeatSpec(hp=4)],
        deck=deck_excluding(PACK, SLASH),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    card = state.card(SLASH)
    if virtual == "transformed":
        card = transformed_card(state, (SLASH,), "duel")
    elif virtual == "pure":
        card = pure_virtual_card(state, "dodge")
    engine.start_use(card, source=0, targets=(1,))
    return engine


def test_the_projection_derives_from_the_real_engine_buffer():
    engine = _engine_with_use()

    events = card_events_after(
        engine.records, after=-1, state=engine.state, events=engine.card_events
    )

    assert events == [
        {
            "seq": engine.card_events[0].seq,
            "seat": 0,
            "card_type": "slash",
            "card_name": "杀",
            "gender": "male",
            "virtual": False,
        }
    ]


def test_the_projection_ships_the_product_type_of_transformed_and_virtual_cards():
    """转化 / 纯虚拟 ⇒ 播**产物**的语音（不是来源实体牌）。"""
    transformed = _engine_with_use(virtual="transformed")
    pure = _engine_with_use(virtual="pure")

    converted = card_events_after(
        transformed.records,
        after=-1,
        state=transformed.state,
        events=transformed.card_events,
    )[0]
    created = card_events_after(
        pure.records, after=-1, state=pure.state, events=pure.card_events
    )[0]

    assert (converted["card_type"], converted["virtual"]) == ("duel", True)
    assert converted["card_name"] == "决斗"
    assert (created["card_type"], created["virtual"]) == ("dodge", True), (
        "【八卦阵】视为【闪】：播【闪】"
    )


def test_a_static_replay_without_an_engine_buffer_yields_an_empty_list():
    """只喂静态 jsonl 录像的读取方（不重跑引擎）：**空列表、不抛**。"""
    engine = _engine_with_use()
    # 真录像是「逐行 JSONL」：这里把自己跑出来的记录流序列化再读回来，形状与录像文件一致。
    static_records = [json.loads(line) for line in dump_journal(engine.records).splitlines()]
    assert static_records, "至少要有记录，别让这条用例退化成「空列表本来就空」"

    assert card_events_after(static_records, after=-1, state=engine.state) == [], (
        "没有引擎缓冲 ⇒ 这一路表现缺席，不是错误"
    )
    assert card_events_after(static_records, after=-1, state=engine.state, events=()) == []


def test_the_projection_never_emits_an_event_past_the_records_it_was_given():
    """``records`` 定本批上界：锚点记录还没进这批 ⇒ 留到下一次轮询，不提前出声。

    用**真会话**（锚点前面已经有一整批记录）：人为局面里 ``start_use`` 就是第一条带序号的
    记录，截不出"锚点之前的上界"，测不到这条判据。
    """
    _registry, session = _session_with_card_use()
    engine = session.engine
    anchor = engine.card_events[0].seq
    before_anchor = [
        record
        for record in engine.records
        if getattr(record, "seq", None) is None or record.seq < anchor
    ]
    assert before_anchor, "这条用例要求锚点之前确实还有记录"

    assert (
        card_events_after(
            before_anchor, after=-1, state=engine.state, events=engine.card_events
        )
        == []
    ), "锚点记录还没进这批 ⇒ 这一批不下发（下一次轮询时 after 仍小于它，一条都不会丢）"
    assert card_events_after(
        engine.records, after=-1, state=engine.state, events=engine.card_events
    ), "同一批数据在完整记录流上照常下发"


def test_the_projection_drops_events_whose_seat_is_outside_this_game():
    """坏数据 fail-closed：座位越界的条目不下发（前端据此找不到使用者）。"""
    engine = _engine_with_use()

    assert (
        card_events_after(
            engine.records,
            after=-1,
            state=engine.state,
            events=[
                CardUseEvent(
                    seq=10**6,
                    seat=99,
                    card_type="slash",
                    card_name="杀",
                    gender=Gender.MALE,
                    virtual=False,
                )
            ],
        )
        == []
    )


def test_a_gender_less_fact_is_shipped_as_null_not_guessed():
    """无武将模式 ⇒ ``gender: null``（前端回落 ``male``；服务端不替它猜）。"""
    engine = _engine_with_use(gender=None)

    events = card_events_after(
        engine.records, after=-1, state=engine.state, events=engine.card_events
    )

    assert events[0]["gender"] is None
