"""伤害事实的接入层下发（damage-fx 票 02 / spec §Solution 2）。

口径（票面「验收」逐字 + spec §硬约束）：

- 会话载荷多一个 ``damage_events`` 键，形状
  ``{seq, source, target, amount, nature}``，与 ``skill_events`` **同一套游标语义**；
- ``after`` 等于某条的 ``seq`` ⇒ 再取为空（游标语义，轮询不重复下发）；
- 静态录像（没有 ``Engine.damage_events``）⇒ **空列表、不抛错**；
- 不按视角过滤：伤害是公开事实（血条本来就公开），与技能名同一档。

两条取数路径（``CODING_STANDARDS.md`` §6）：主用例走**真会话**（``GameRegistry.create`` +
``view``，AI 座位真的打出一次伤害），其余走真引擎 / 静态录像喂进同一个投影函数。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from sgs.data import load_content_pack  # noqa: E402
from sgs.effects import DamageNature  # noqa: E402
from sgs.engine import Engine, dump_journal  # noqa: E402
from sgs.engine.game import DamageEvent  # noqa: E402
from sgs.state import EndPhase, FrameKind, Pass  # noqa: E402
from sgs.timing import Timing  # noqa: E402
from sgs_web.projection import damage_events_after  # noqa: E402
from sgs_web.serve import GameRegistry  # noqa: E402

from support import (  # noqa: E402
    PassPlayer,
    SeatSpec,
    build_state,
    deck_excluding,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")

ANSWER_SEED = 10
"""2 人会话里 AI 座位**第 3 次**应答后就打出一次伤害的种子（见 ``_session_with_damage``）。

固定种子 + 固定应答规则 = 确定的记录流；本文件只借它拿一次**真实**伤害，不模拟具体牌局。
"""


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


def _session_with_damage(seed: int = ANSWER_SEED, *, limit: int = 200):
    """推进真会话直到引擎记下**第一条**伤害事实（人类座位全程最小应答：不出闪）。"""
    registry = GameRegistry(runs_dir="/tmp/unused")
    session = registry.create(
        {"player_count": 2, "ai": "random", "seed": seed, "timeout_seconds": None}
    )
    for _ in range(limit):
        if session.engine.damage_events:
            return registry, session
        if session.pending is None:
            session.drive()
            if session.finished:
                break
            continue
        session.submit_answer(_answer(session.pending))
        if session.finished:
            break
    raise AssertionError(f"种子 {seed} 在 {limit} 步内没有打出伤害——换种子或改应答规则")


# ---------------------------------------------------------------------------
# 真会话：载荷多一个 damage_events 键
# ---------------------------------------------------------------------------


def test_a_real_session_payload_carries_the_damage_fact_with_all_five_fields():
    registry, session = _session_with_damage()

    body = registry.view(session, after=-1, logs_after=0)

    assert "damage_events" in body, "会话载荷必须有这个键（前端按它排队演出）"
    events = body["damage_events"]
    assert len(events) == 1, f"这一次伤害恰好下发一条，实际：{events}"
    event = events[0]
    assert set(event) == {"seq", "source", "target", "amount", "nature"}, (
        f"形状必须稳定（前端按它量起 / 落点）：{event}"
    )
    engine_event = session.engine.damage_events[0]
    assert event["seq"] == engine_event.seq, "锚点 = 引擎记下的那条记录的序号"
    assert (event["source"], event["target"]) == (
        engine_event.source,
        engine_event.target,
    ), "来源 / 目标原样下发（剑气从来源那张武将牌飞到中剑那张）"
    assert event["amount"] == engine_event.amount >= 1
    assert event["nature"] == DamageNature.NORMAL.value, "无属性【杀】⇒ 字符串 'NORMAL'"
    assert 0 <= event["source"] < 2 and 0 <= event["target"] < 2, (
        "两条座位都是本局真实座位（前端据此找武将块）"
    )


def test_the_cursor_is_the_same_one_as_the_records_batch():
    """``after`` 游标语义：取过一条之后，用它自己的 ``seq`` 再取就是空。"""
    registry, session = _session_with_damage()
    first = registry.view(session, after=-1, logs_after=0)
    event = first["damage_events"][0]

    again = registry.view(session, after=event["seq"], logs_after=0)
    assert again["damage_events"] == [], "同一条不允许重复下发（轮询不重放剑光）"

    # 同一个游标走两次也还是空：伤害事件是**派生通道**，不进记录流、不会改变游标。
    idle = registry.view(session, after=event["seq"], logs_after=0)
    assert idle["damage_events"] == []
    assert idle["cursor"] == again["cursor"], "派生通道不推游标"


def test_the_key_is_present_before_any_damage_happens():
    """开局那一批没有伤害事实 ⇒ 键仍在，值是空列表（前端不用判 ``undefined``）。"""
    registry = GameRegistry(runs_dir="/tmp/unused")
    session = registry.create(
        {"player_count": 2, "ai": "random", "seed": ANSWER_SEED, "timeout_seconds": None}
    )

    body = registry.view(session, after=-1, logs_after=0)

    assert body["damage_events"] == []


def test_the_damage_channel_does_not_change_the_record_stream():
    """派生通道的硬约束：它不往记录流里加一条，金录像 / 存档形状因此一字不变。"""
    registry, session = _session_with_damage()

    body = registry.view(session, after=-1, logs_after=0)

    assert body["damage_events"], "先确认这一批里真的有伤害事实"
    assert all(record.get("kind") != "damage_event" for record in body["records"]), (
        "伤害事实不是记录流的一条记录（进了就会改 47 条金录像的字节）"
    )
    lines = dump_journal(session.engine.records).splitlines()
    assert lines and all(json.loads(line) for line in lines), "记录流仍是可解析的 canonical JSONL"


# ---------------------------------------------------------------------------
# 投影函数本身：游标 / 上界 / 座位 / 兜底
# ---------------------------------------------------------------------------


def _engine_with_damage(*, amount: int = 1, nature=DamageNature.NORMAL) -> Engine:
    """真引擎 + 一条与【杀】命中同形的 ``DAMAGE`` 子帧（先例见 ``tests/test_armor.py``）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hp_max=8)],
        deck=deck_excluding(PACK),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 0,
            "targets": (1,),
            "card": None,
            "use_timing": None,
            "target": 1,
            "channel": None,
            "nature": nature,
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.DAMAGE_END),
    )
    return engine


def test_the_projection_derives_from_the_real_engine_buffer():
    engine = _engine_with_damage(amount=2)

    events = damage_events_after(
        engine.records, after=-1, state=engine.state, events=engine.damage_events
    )

    assert len(events) == 1
    assert events[0] == {
        "seq": engine.damage_events[0].seq,
        "source": 0,
        "target": 1,
        "amount": 2,
        "nature": "NORMAL",
    }


def test_a_static_replay_without_an_engine_buffer_yields_an_empty_list():
    """只喂静态 jsonl 录像的读取方（不重跑引擎）：**空列表、不抛**（票面「别踩的坑」）。"""
    engine = _engine_with_damage()
    # 真录像是「逐行 JSONL」：这里把自己跑出来的记录流序列化再读回来，形状与录像文件一致。
    static_records = [json.loads(line) for line in dump_journal(engine.records).splitlines()]
    assert static_records, "至少要有记录，别让这条用例退化成「空列表本来就空」"

    assert damage_events_after(static_records, after=-1, state=engine.state) == [], (
        "没有引擎缓冲 ⇒ 这一路表现缺席，不是错误"
    )
    assert damage_events_after(static_records, after=-1, state=engine.state, events=()) == []


def test_the_projection_never_emits_an_event_past_the_records_it_was_given():
    """``records`` 定本批上界：锚点记录还没进这批 ⇒ 留到下一次轮询，不提前下发。"""
    engine = _engine_with_damage()
    anchor = engine.damage_events[0].seq
    before_anchor = [
        record
        for record in engine.records
        if getattr(record, "seq", None) is None or record.seq < anchor
    ]
    assert before_anchor, "这条用例要求锚点之前确实还有记录"

    assert (
        damage_events_after(before_anchor, after=-1, state=engine.state, events=engine.damage_events)
        == []
    ), "锚点记录还没进这批 ⇒ 这一批不下发（下一次轮询时 after 仍小于它，一条都不会丢）"
    assert damage_events_after(
        engine.records, after=-1, state=engine.state, events=engine.damage_events
    ), "同一批数据在完整记录流上照常下发"


def test_the_projection_drops_events_whose_seats_are_outside_this_game():
    """坏数据 fail-closed：座位越界的目标 / 来源不下发（前端据此量不出武将牌）。"""
    engine = _engine_with_damage()

    assert (
        damage_events_after(
            engine.records,
            after=-1,
            state=engine.state,
            events=[DamageEvent(seq=10**6, source=0, target=99, amount=1, nature=DamageNature.NORMAL)],
        )
        == []
    )
    assert (
        damage_events_after(
            engine.records,
            after=-1,
            state=engine.state,
            events=[DamageEvent(seq=10**6, source=99, target=1, amount=1, nature=DamageNature.NORMAL)],
        )
        == []
    )


def test_a_nature_less_fact_is_shipped_as_null_not_guessed():
    engine = _engine_with_damage(nature=None)

    events = damage_events_after(
        engine.records, after=-1, state=engine.state, events=engine.damage_events
    )

    assert events[0]["nature"] is None, "缺属性原样给 null（前端只播 'NORMAL'）"
