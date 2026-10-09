"""时机覆盖清单成为机械判决（票 25 / 票 12 §5 / 票 32 §3）。

覆盖清单 = 「时机节点 → 覆盖它的基准或用例」的映射，定义在
``tests/support/timing_coverage.py``。本文件把它变成判决，断言四件事：

1. 清单的键集**恰等于** M2 的 dispatch 白名单（53 个可从记录流观测的节点）；
2. 清单里声明的**每一个**来源都真的 dispatch 它声称覆盖的节点（实测，不靠声明）；
3. 所有来源的记录流并集**恰等于**白名单（既 ⊇ 也不多）；
4. 19 个枚举位与 11 个空日志节点**一个都不出现**在任何记录流里。

**"⊇ 64" 的重述**（票 18 已登记、票 25 落地、票 32 定格）：64 个已实现类里有 11 个 L2
边界 / 阶段间节点结构性地不 dispatch、记录流不可见，故机械判据是
「53 个可见节点 == 白名单」+「11 个空日志节点结构性存在且从不出现」。
枚举本身的两组划分见 ``tests/test_timing.py``，这里只管"实测覆盖"。
"""

from __future__ import annotations

from sgs.timing import DISPATCH_WHITELIST, EMPTY_LOG, ENUM_ONLY, IMPLEMENTED, Timing

from support.timing_coverage import ALL_SOURCES, COVERAGE, dispatched

WHITELIST = frozenset(timing.value for timing in DISPATCH_WHITELIST)
"""53 个节点按**字符串取值**比较：与记录流里的 ``Timing`` 无关，不受 import 路径影响。"""


def _collected() -> frozenset[str]:
    """所有覆盖来源的记录流里实测出现的时机节点（字符串取值）。"""
    return frozenset().union(*(dispatched(source) for source in ALL_SOURCES))


def test_the_coverage_list_keys_are_exactly_the_dispatch_whitelist():
    assert frozenset(timing.value for timing in COVERAGE) == WHITELIST


def test_every_declared_source_really_dispatches_the_node_it_covers():
    missed = {
        timing.value: [source for source in sources if timing.value not in dispatched(source)]
        for timing, sources in COVERAGE.items()
    }

    assert {node: sources for node, sources in missed.items() if sources} == {}, (
        "覆盖清单里声明了却实测未命中的节点：回改清单并说明原因（票 25）"
    )


def test_the_union_of_actual_record_streams_is_exactly_the_dispatch_whitelist():
    collected = _collected()

    assert collected >= WHITELIST, "64 个已实现里可从记录流观测的那些，一个不漏"
    assert collected == WHITELIST, "也不得多：白名单之外的节点不得被 dispatch"


def test_the_dispatch_whitelist_and_the_empty_log_nodes_restate_the_64():
    assert len(WHITELIST) == 53
    assert len(EMPTY_LOG) == 11
    assert WHITELIST | {timing.value for timing in EMPTY_LOG} == {
        timing.value for timing in IMPLEMENTED
    }


def test_enum_only_and_empty_log_nodes_never_appear_in_any_record_stream():
    """实测判据：来源覆盖 M2 的全部行为面（回合环 / 使用 / 伤害 / 濒死 / 死亡 / 回复 /
    重洗 / 判定 / 身份 / 打出），故"一个不冒"是跑出来的，不是声明出来的。

    引擎侧**不**加白名单守卫：记录流 schema 对 83 个节点都合法（后续里程碑填位用），
    把常量硬编码进 ``Engine`` 会让后续的 dispatch 被误挡。
    """
    collected = _collected()

    assert collected.isdisjoint({timing.value for timing in ENUM_ONLY}), (
        "19 个枚举位只留位，M2 一次都不 dispatch"
    )
    assert collected.isdisjoint({timing.value for timing in EMPTY_LOG}), (
        "11 个空日志节点结构性存在，但记录流里不出现"
    )


def test_a_judge_phase_with_an_empty_judge_zone_dispatches_no_judge_events():
    """判定区为空 ⇒ 阶段体照记、不发判定事件；边界节点结构性不发（票 26 起判定阶段
    只在**有可结算的延时锦囊**时才 dispatch L14，故这里用 turn-loop 这个空判定区的局面）。"""
    stream = dispatched("position:turn-loop")

    assert Timing.PHASE_JUDGE.value in stream
    assert not {value for value in stream if value.startswith("JUDGE_")}
    assert Timing.PHASE_JUDGE_START.value not in stream
    assert Timing.PHASE_JUDGE_END.value not in stream
