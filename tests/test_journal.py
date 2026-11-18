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
        "mode": "no_identity",
        "pve": None,
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


# ---------------------------------------------------------------------------
# header v3：`mode` / `pve` 进 header，v2 档靠缺省值兼容（M7 票 07）
# ---------------------------------------------------------------------------


def test_the_event_schema_version_is_three_and_reads_the_previous_one():
    """票 01 §2 / M7 票 07：升到 3，但**读得懂** v2——旧 ``runs/`` 录像继续可播。"""
    from sgs.engine import SUPPORTED_EVENT_VERSIONS

    assert EVENT_SCHEMA_VERSION == 3
    assert SUPPORTED_EVENT_VERSIONS == frozenset({2, 3}), (
        "v2 必须在支持集合里：仓库外的旧录像与 runs/ 下的旧局都还是 v2"
    )


def _v2_header_line(text: str) -> str:
    """把一条 v3 记录流的首行改写成 v2 的形状：版本号回退、``rule_config`` 里没有新键。"""
    line, rest = text.split("\n", 1)
    payload = json.loads(line)
    payload["event_schema_version"] = 2
    payload["rule_config"].pop("mode", None)
    payload["rule_config"].pop("pve", None)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        + rest
    )


def test_a_v2_header_reads_back_with_the_derived_mode_and_no_pve_section(tmp_path):
    """v2 档的兼容只有一段**缺省值**：``mode`` 按座位数派生、``pve`` 补 ``None``。

    不另建 v1 式读取器（票 01 §2）：``RuleConfig`` 的 before 校验器把缺省的 ``mode``
    补成派生值，故 v2 文件走的是与 v3 完全相同的解析路径。
    """
    source = play_standard(seed=7)
    v2_text = _v2_header_line(source.journal())
    path = tmp_path / "v2.jsonl"
    path.write_text(v2_text, encoding="utf-8")

    header = read_journal(path)[0]

    assert header.event_schema_version == 2, "文件里写的是几就是几（不静默改写版本号）"
    assert header.rule_config.player_count == 2
    assert header.rule_config.mode.value == "no_identity", "v2 的 mode 按座位数派生"
    assert header.rule_config.pve is None, "v2 没有 PvE 那一节"
    assert dump_journal([header]) != v2_text.split("\n", 1)[0] + "\n", (
        "读回后按当前形状 dump 会补出新键——旧档升形只发生在内存里，从不写回文件"
    )
