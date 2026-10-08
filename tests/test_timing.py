"""时机枚举的完整性：权威表恰 83 个节点、层号派生、M1 两组划分。

枚举本身是票 01 冻结数据的落地；这里只断言"表没被写错"（数量、分区、伪时机缺席），
"哪些节点真的被 dispatch"由后续票据的覆盖清单断言。
"""

from __future__ import annotations

from sgs.timing import FORBIDDEN_TIMING_NAMES, M1_ENUM_ONLY, M1_IMPLEMENTED, Timing


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
    implemented = set(M1_IMPLEMENTED)
    enum_only = set(M1_ENUM_ONLY)

    assert len(implemented) == 58, "票 01 §C (a) 类恰 58 个节点"
    assert len(enum_only) == 25, "票 01 §C (b) 类恰 25 个节点"
    assert implemented | enum_only == set(Timing)
    assert implemented & enum_only == set()


def test_pseudo_timing_names_are_not_members():
    member_names = {timing.value for timing in Timing}

    for forbidden in FORBIDDEN_TIMING_NAMES:
        assert forbidden not in member_names
    assert "濒死" in FORBIDDEN_TIMING_NAMES
    assert len(FORBIDDEN_TIMING_NAMES) == 7
