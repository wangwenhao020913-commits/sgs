"""时机枚举的完整性：权威表恰 83 个节点、层号派生、M2 三分区、伪时机禁令。

枚举本身是票 01 冻结数据的落地；这里只断言"表没被写错"（数量、分区、伪时机禁令、
标识符只存枚举标识），"哪些节点真的被 dispatch"由 ``tests/test_timing_coverage.py``
的覆盖清单断言。

M2 定格（票 17 §3 / 票 32）把分区改为里程碑中性名：

- ``DISPATCH_WHITELIST``（53）：真的会 dispatch、可在记录流里观测到的节点；
- ``EMPTY_LOG``（11）：已实现类里**空日志**的 L2 边界与阶段间节点——枚举位保留，
  但流程结构性地不经过、因此记录流里一个都不出现；
- ``ENUM_ONLY``（19）：枚举位保留、流程走不到。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sgs.engine import TimingRecord, record_json
from sgs.timing import (
    DISPATCH_WHITELIST,
    EMPTY_LOG,
    ENUM_ONLY,
    FORBIDDEN_TIMING_NAMES,
    IMPLEMENTED,
    Timing,
)


def test_enum_has_exactly_83_nodes():
    assert len(Timing) == 83


def test_value_is_the_bare_identifier_so_layer_never_lands_on_disk():
    assert Timing.RESOLVE_BEFORE_EFFECT.value == "RESOLVE_BEFORE_EFFECT"


def test_layer_and_order_are_derived_from_the_enum():
    assert Timing.RESOLVE_BEFORE_EFFECT.label == "L5.2 RESOLVE_BEFORE_EFFECT"
    assert Timing.GAME_START.label == "L0.3 GAME_START"
    assert Timing.TURN_END.label == "L2.24 TURN_END"
    assert Timing.PHASE_JUDGE.label == "L2.6 PHASE_JUDGE"


def test_implemented_and_enum_only_partition_the_whole_table():
    implemented = set(IMPLEMENTED)
    enum_only = set(ENUM_ONLY)

    assert len(implemented) == 64, "M2 已实现类恰 64 个节点（票 17 §3）"
    assert len(enum_only) == 19, "M2 只留枚举位类恰 19 个节点（票 17 §3）"
    assert implemented | enum_only == set(Timing)
    assert implemented & enum_only == set()


def test_the_dispatch_whitelist_and_empty_log_nodes_partition_the_implemented_set():
    dispatch = set(DISPATCH_WHITELIST)
    empty_log = set(EMPTY_LOG)

    assert len(dispatch) == 53, "M2 dispatch 白名单恰 53 个（记录流可观测）"
    assert len(empty_log) == 11, "空日志 L2 节点恰 11 个"
    assert dispatch.isdisjoint(empty_log)
    assert dispatch | empty_log == set(IMPLEMENTED), "53 + 11 复原 64 个已实现类"
    assert dispatch | empty_log | set(ENUM_ONLY) == set(Timing)


def test_the_six_m2_nodes_are_dispatchable():
    """票 01 §6 的 6 个 enum-only 转 dispatch（票 17 §3 / 票 32）。"""
    six = {
        Timing.JUDGE_ON,
        Timing.JUDGE_CARD_BEFORE_EFFECT,
        Timing.JUDGE_CARD_AFTER_EFFECT,
        Timing.DEATH_REVEAL_IDENTITY_BEFORE,
        Timing.PLAY_DECLARE,
        Timing.PLAY_ON_PLAY,
    }

    assert set(DISPATCH_WHITELIST) >= six
    assert six.isdisjoint(ENUM_ONLY)


def test_the_empty_log_nodes_are_the_l2_boundaries_and_inter_phase_nodes():
    """票 25 实测：11 个节点从不进记录流，它们是 L2 边界与 5 个阶段间。"""
    assert set(EMPTY_LOG) == {
        Timing.PHASE_PREPARE_START,
        Timing.PHASE_PREPARE_END,
        Timing.INTERVAL_PREPARE_TO_JUDGE,
        Timing.PHASE_JUDGE_START,
        Timing.PHASE_JUDGE_END,
        Timing.INTERVAL_JUDGE_TO_DRAW,
        Timing.INTERVAL_DRAW_TO_PLAY,
        Timing.INTERVAL_PLAY_TO_DISCARD,
        Timing.INTERVAL_DISCARD_TO_FINISH,
        Timing.PHASE_FINISH_START,
        Timing.PHASE_FINISH_END,
    }


def test_no_timing_constant_keeps_the_m1_prefix():
    """票 17 §3：常量改里程碑中性名，避免 M1 / M2 双真源。"""
    import sgs.timing as timing_module

    assert not [name for name in dir(timing_module) if name.startswith("M1_")]


def test_pseudo_timing_names_are_not_members():
    member_names = {timing.value for timing in Timing}

    for forbidden in FORBIDDEN_TIMING_NAMES:
        assert forbidden not in member_names
    assert "濒死" in FORBIDDEN_TIMING_NAMES
    assert len(FORBIDDEN_TIMING_NAMES) == 7


def test_pseudo_timing_names_are_rejected_as_enum_values():
    """禁令不只是"缺席"：拿伪时机当枚举值必须直接失败（票 25）。"""
    for forbidden in FORBIDDEN_TIMING_NAMES:
        with pytest.raises(ValueError):
            Timing(forbidden)


def test_pseudo_timing_names_are_rejected_by_record_validation():
    """记录流 schema 也不接受伪时机：``Timing`` 字段的枚举校验直接拒绝。"""
    for forbidden in FORBIDDEN_TIMING_NAMES:
        with pytest.raises(ValidationError):
            TimingRecord(seq=1, timing=forbidden, frame_id=0, depth=0)


def test_timing_records_store_only_the_enum_identifier_not_the_rendered_label():
    """层号由枚举派生（``label`` 只在显示层）；记录字段里只有枚举标识。"""
    record = TimingRecord(seq=1, timing=Timing.RESOLVE_BEFORE_EFFECT, frame_id=0, depth=0)

    payload = json.loads(record_json(record))
    assert payload["timing"] == "RESOLVE_BEFORE_EFFECT"
    assert Timing.RESOLVE_BEFORE_EFFECT.label == "L5.2 RESOLVE_BEFORE_EFFECT"
    assert "L5.2" not in record_json(record), "渲染形式不得进记录字段"
    for derived in ("label", "layer", "order"):
        assert derived not in payload
        assert derived not in TimingRecord.model_fields
