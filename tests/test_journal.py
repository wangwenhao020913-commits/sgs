"""记录流 schema 与 canonical JSONL 序列化器（票 06 / 票 10 §5）。

落点 = 库门面（主缝）产出的 journal + 唯一序列化器 ``dump_journal``。
"""

from __future__ import annotations

import json

from sgs.engine import (
    EVENT_SCHEMA_VERSION,
    CardMoveRecord,
    FrameRecord,
    GameEndRecord,
    Header,
    QueryRecord,
    TimingRecord,
    dump_journal,
    record_json,
    read_journal,
)
from sgs.state import FrameKind, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from support import play_standard


def test_header_is_the_first_line_and_carries_the_envelope():
    engine = play_standard(seed=7)
    header = engine.records[0]

    assert isinstance(header, Header)
    assert header.seed == 7
    assert header.pack_id == "standard"
    assert header.deck_profile == "basic_53"
    assert header.event_schema_version == EVENT_SCHEMA_VERSION
    assert header.rule_config.model_dump(mode="json") == {
        "player_count": 2,
        "starting_hand_size": 4,
        "first_seat": 0,
    }
    assert "seq" not in header.model_dump(mode="json")


def test_every_record_after_the_header_has_a_strictly_increasing_seq():
    engine = play_standard(seed=7)
    seqs = [record.seq for record in engine.records[1:]]

    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs), "全流单一 seq，不得重复"
    assert seqs[0] == 1


def test_the_seven_kinds_are_all_defined_and_serialisable():
    """七个 kind 齐备；随机玩家会真的用牌，故整局里 `frame` 记录也出现。

    随机驱动的一局必达 ``game_end``（票 22 / 票 23）；``seed = 8`` 这一局七种记录齐备
    （重洗落地后它以胜负收局，不再是"牌堆耗尽平局"）。濒死与死亡的逐条断言分别在
    ``tests/test_near_death.py`` / ``tests/test_death.py``。
    """
    produced = {record.kind for record in play_standard(seed=8).records}

    assert produced == {"header", "timing", "delta", "query", "action", "frame", "game_end"}

    synthetic = [
        Header(seed=1, pack_id="standard", deck_profile="basic_53", rule_config=_rule_config()),
        TimingRecord(seq=1, timing=Timing.GAME_START, frame_id=0, depth=0),
        CardMoveRecord(
            seq=2,
            frame_id=0,
            depth=0,
            cards=(),
            from_zone=ZoneRef(Zone.DECK),
            to_zone=ZoneRef(Zone.HAND, 0),
            reason=MoveReason.DRAW,
        ),
        FrameRecord(
            seq=3,
            op="push",
            frame_id=1,
            frame_kind=FrameKind.USE,
            depth=1,
            return_to=("RESOLVE", "RESOLVE_AFTER_EFFECT"),
        ),
        GameEndRecord(seq=4, result="draw", winner=None, reason="both_decks_empty", check_seq=2),
    ]
    lines = dump_journal([*synthetic, *_query_records()])

    assert {json.loads(line)["kind"] for line in lines.splitlines()} == {
        "header",
        "timing",
        "delta",
        "frame",
        "game_end",
        "query",
        "action",
    }


def test_canonical_jsonl_is_lf_sorted_compact_unescaped_and_newline_terminated():
    text = play_standard(seed=7).journal()

    assert "\r" not in text
    assert text.endswith("\n")
    lines = text.splitlines()
    assert len(lines) == len(play_standard(seed=7).records)

    header_line = lines[0]
    parsed = json.loads(header_line)
    assert header_line == json.dumps(
        parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ), "canonical 化必须是 sort_keys + 紧凑分隔符 + 不转义中文"

    query_line = next(line for line in lines if '"kind":"query"' in line)
    assert "出牌阶段" in query_line, "prompt 里的中文不得被转义"


def test_same_seed_twice_is_byte_identical():
    first = play_standard(seed=20261008).journal()
    second = play_standard(seed=20261008).journal()

    assert first == second


def test_the_opening_records_are_the_initial_shuffle_then_the_deal():
    engine = play_standard(seed=7)
    header, shuffle, dealing, deal_seat_0, deal_seat_1 = engine.records[:5]

    assert isinstance(header, Header)
    assert shuffle.kind == "delta" and shuffle.delta_type == "deck_shuffle"
    assert shuffle.seq == 1, "seq = 1 必是初次洗牌（票 08 §5）"
    assert shuffle.frame_id == 0, "无帧哨兵：引擎初始化"
    assert shuffle.from_zone is None, "初次洗牌不来自弃牌堆"
    assert shuffle.to_zone.zone.value == "deck"
    assert len(shuffle.cards) == 53

    assert isinstance(dealing, TimingRecord)
    assert dealing.timing is Timing.GAME_INITIAL_HAND_DEALING
    assert dealing.frame_id == 0

    for seat, deal in enumerate((deal_seat_0, deal_seat_1)):
        assert deal.delta_type == "card_move"
        assert deal.reason is MoveReason.DRAW
        assert deal.to_zone.zone.value == "hand"
        assert len(deal.cards) == 4, "按座序每个座位发 4 张"
        assert [card.card_id for card in deal.cards] == [
            card.card_id for card in shuffle.cards[seat * 4 : seat * 4 + 4]
        ]


def test_different_seeds_produce_different_journals():
    assert play_standard(seed=1).journal() != play_standard(seed=2).journal()


def _rule_config():
    from sgs.data import RuleConfig

    return RuleConfig()


def _query_records():
    engine = play_standard(seed=7)
    records = [record for record in engine.records if record.kind in {"query", "action"}]
    assert isinstance(records[0], QueryRecord)
    return records


def test_a_card_move_carries_the_landing_spot_only_when_it_is_not_the_default(tmp_path):
    """票 02：进牌堆的落点是**可选事实**——缺省不落盘，显式置顶才写 ``to_top``。

    记录形状在这里定死（``CardMoveDelta.to_top``）：

    - 缺省 ``False`` 时**一个字节都不多**：70 条既有录像的每条 ``card_move`` 都长这样，
      读回再 dump 必须逐字节相同（``tests/test_replays.py`` 的"录制 == 重放"是同一判据）；
    - ``True`` 才写 ``"to_top":true``，且这条路也要能原样读回、原样 dump——fuzz 重建器只
      读这份记录，不再靠"看见 ``SelectOption(deck_top)``"去猜落点。
    """
    engine = play_standard(seed=7)
    header = engine.records[0]
    move = next(
        record for record in engine.records if getattr(record, "delta_type", None) == "card_move"
    )
    assert isinstance(move, CardMoveRecord)
    landed = CardMoveRecord(
        seq=move.seq,
        frame_id=move.frame_id,
        depth=move.depth,
        cards=move.cards,
        from_zone=move.from_zone,
        to_zone=move.to_zone,
        reason=move.reason,
        owner=move.owner,
        to_top=True,
    )

    assert "to_top" not in record_json(move), "缺省落点不落盘（旧文件逐字节可逆）"
    assert '"to_top":true' in record_json(landed), "显式落点写在记录里（记录自描述）"

    for record, to_top in ((move, False), (landed, True)):
        path = tmp_path / f"card_move-{to_top}.jsonl"
        path.write_text(dump_journal([header, record]), encoding="utf-8")
        replayed = read_journal(path)
        assert replayed[1].to_top is to_top
        assert dump_journal(replayed) == path.read_text(encoding="utf-8"), (
            "读回再 dump 必须逐字节相同（缺省与新形状两侧都要）"
        )
