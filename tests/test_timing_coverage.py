"""时机覆盖清单成为机械判决（票 25 / 票 12 §5 / 票 32 §3）。

覆盖清单 = 「时机节点 → 覆盖它的基准或用例」的映射，定义在
``tests/support/timing_coverage.py``。本文件把它变成判决，断言四件事：

1. 清单的键集**恰等于** dispatch 白名单（56 个可从记录流观测的节点）；
2. 清单里声明的**每一个**来源都真的 dispatch 它声称覆盖的节点（实测，不靠声明）；
3. 所有来源的记录流并集**恰等于**白名单（既 ⊇ 也不多）；
4. 16 个枚举位与 11 个空日志节点**一个都不出现**在任何记录流里。

**"⊇ 64" 的重述**（票 18 已登记、票 25 落地、票 32 定格）：64 个已实现类里有 11 个 L2
边界 / 阶段间节点结构性地不 dispatch、记录流不可见，故机械判据是
「53 个 M2 可见节点 == 白名单 − 解封批次」+「11 个空日志节点结构性存在且从不出现」。
枚举本身的两组划分见 ``tests/test_timing.py``，这里只管"实测覆盖"。
"""

from __future__ import annotations

from sgs.timing import DISPATCH_WHITELIST, EMPTY_LOG, ENUM_ONLY, IMPLEMENTED, Timing

from support.timing_coverage import ALL_SOURCES, COVERAGE, PINDIAN_SCENE, dispatched

WHITELIST = frozenset(timing.value for timing in DISPATCH_WHITELIST)
"""56 个节点按**字符串取值**比较：与记录流里的 ``Timing`` 无关，不受 import 路径影响。"""


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

    assert collected >= WHITELIST, "67 个已实现里可从记录流观测的那些，一个不漏"
    assert collected == WHITELIST, "也不得多：白名单之外的节点不得被 dispatch"


M3_UNSEALED: frozenset[str] = frozenset({Timing.MOVE_AFTER.value})
"""M3 的解封增量（票 07 / ADR-010 的"解封集合 = 白名单增量"）。"""

WUMING_UNSEALED: frozenset[str] = frozenset(
    {Timing.MAXLIFE_INCREASE_BEFORE.value, Timing.MAXLIFE_INCREASE_AFTER.value}
)
"""wuming 票 04 的解封增量：L11 的**加**体力上限二时机（减侧两个不解封，见 C4）。"""

PINDIAN_UNSEALED: frozenset[str] = frozenset(
    {Timing.RANK_COMPARE_ON.value, Timing.RANK_COMPARE_RESULT_AFTER.value}
)
"""huo-taishici-xunyu 票 03 的解封增量：L15 的拼点二时机（RANK_COMPARE 帧的步骤表派发）。"""

TAOCAOCAO_UNSEALED: frozenset[str] = frozenset({Timing.RANK_COMPARE_PLACED.value})
"""tao-caocao 票 03 的增量：L15 新增并解封的「拼点牌扣置入处理区后、亮出前」时机。"""

FENGYUJIN_UNSEALED: frozenset[str] = frozenset({Timing.ROUND_START.value})
"""feng-yujin 票 02 的增量：L1.0 **新增**的「每轮开始时」（轮边界；首个载体 = 锋·于禁【定乱】）。"""

M2_DISPATCH_WHITELIST = frozenset(
    timing.value
    for timing in DISPATCH_WHITELIST
    if timing.value
    not in M3_UNSEALED | WUMING_UNSEALED | PINDIAN_UNSEALED | TAOCAOCAO_UNSEALED
    | FENGYUJIN_UNSEALED
)
"""M2 的 53 个：今天的白名单减去五批解封（M3 一个 + wuming 两个 + 拼点两个 +
拼点扣置一个 + 轮边界一个）。"""


def test_the_unsealed_set_is_exactly_the_move_after_node():
    """ADR-010 的机械判据：M3 的 ``解封集合 = 白名单增量 = {MOVE_AFTER}``（票 07）。

    M3 的那批**只解封 after**——``MOVE_BEFORE`` 继续留在枚举位。此后的解封批次由
    :data:`WUMING_UNSEALED` 单列（wuming 票 04），故本判据在"白名单 − M2 − wuming 批"
    上仍然成立；"两批之外没有第三个增量"由 M2 基线恒 53 个看住。
    """
    whitelist = {timing.value for timing in DISPATCH_WHITELIST}
    m3_unsealed = (
        whitelist
        - M2_DISPATCH_WHITELIST
        - WUMING_UNSEALED
        - PINDIAN_UNSEALED
        - TAOCAOCAO_UNSEALED
        - FENGYUJIN_UNSEALED
    )

    assert m3_unsealed == {Timing.MOVE_AFTER.value}, "M3 解封的恰是 MOVE_AFTER 一个"
    assert Timing.MOVE_BEFORE.value not in whitelist, "移动前继续留枚举位（M3 无载体）"
    assert len(M2_DISPATCH_WHITELIST) == 53, "M2 基线恰 53 个（解封批次之外没有第三个增量）"
    assert Timing.MOVE_AFTER in COVERAGE, "解封的节点必须有实测来源"


def test_the_wuming_unseal_adds_exactly_the_two_maxlife_increase_nodes():
    """wuming 票 04：解封增量为「加体力上限」二时机；减侧两个**一条也不进**白名单。

    前者的机械含义 = 记录流里真的能看到 ``MAXLIFE_INCREASE_BEFORE/AFTER``（来源 = 构造
    局面 ``position:maxlife``）；后者是 C4 的边界（不迁 ``reduce_hp_max``、不动既有
    觉醒技的 ``hp_max_change.frame_id``）。
    """
    whitelist = {timing.value for timing in DISPATCH_WHITELIST}
    wuming_unsealed = (
        whitelist
        - M2_DISPATCH_WHITELIST
        - M3_UNSEALED
        - PINDIAN_UNSEALED
        - TAOCAOCAO_UNSEALED
        - FENGYUJIN_UNSEALED
    )

    assert wuming_unsealed == WUMING_UNSEALED
    assert {
        Timing.MAXLIFE_DECREASE_BEFORE.value,
        Timing.MAXLIFE_DECREASE_AFTER.value,
    }.isdisjoint(whitelist), "减体力上限二时机继续留枚举位（票 04 C4）"
    for timing in (Timing.MAXLIFE_INCREASE_BEFORE, Timing.MAXLIFE_INCREASE_AFTER):
        assert timing in COVERAGE, "解封的节点必须有实测来源"


def test_the_taocaocao_unseal_is_exactly_the_pindian_placed_node():
    """tao-caocao 票 03：解封增量为「拼点牌扣置后」一个节点，且有实测来源。

    机械含义 = 记录流里真的能看到 ``RANK_COMPARE_PLACED``（来源 = 构造局面
    ``position:pindian`` 的一次拼点）；它必须排在 ``RANK_COMPARE_ON`` 之后、
    ``RANK_COMPARE_RESULT_AFTER`` 之前（扣置后、亮出前）。
    """
    whitelist = {timing.value for timing in DISPATCH_WHITELIST}
    taocaocao_unsealed = (
        whitelist
        - M2_DISPATCH_WHITELIST
        - M3_UNSEALED
        - WUMING_UNSEALED
        - PINDIAN_UNSEALED
        - FENGYUJIN_UNSEALED
    )

    assert taocaocao_unsealed == TAOCAOCAO_UNSEALED
    assert Timing.RANK_COMPARE_PLACED in COVERAGE, "解封的节点必须有实测来源"
    stream = dispatched(PINDIAN_SCENE)
    assert Timing.RANK_COMPARE_PLACED.value in stream, "拼点帧必须真的派发这个时机"


PHASE_START_NODES = (
    Timing.PHASE_PREPARE_START,
    Timing.PHASE_JUDGE_START,
    Timing.PHASE_DRAW_START,
    Timing.PHASE_PLAY_START,
    Timing.PHASE_DISCARD_START,
    Timing.PHASE_FINISH_START,
)
"""六条阶段起点：M2 解封了摸牌 / 出牌 / 弃牌三条，M3 **一条都没再动**（票 14 §6）。"""

M2_PHASE_START_WHITELIST = (
    Timing.PHASE_DRAW_START,
    Timing.PHASE_PLAY_START,
    Timing.PHASE_DISCARD_START,
)
"""M2 就在白名单里的三条阶段起点（摸牌 / 出牌 / 弃牌）；M3 的增量必须为空。"""


def test_m3_unseals_no_phase_start_node():
    """六条 ``PHASE_*_START`` 的解封面与 M2 逐节点相同（票 14 §6）。

    "解封集合 = 白名单增量 = {MOVE_AFTER}" 的集合等式已经钉住总量；这条把票面点名的
    "``PHASE_*_START`` 不动"写成**逐节点的机械判据**：白名单里的阶段起点恰是 M2 的
    摸牌 / 出牌两条，其余四条继续留在空日志节点里。
    """
    whitelist = {timing.value for timing in DISPATCH_WHITELIST}
    empty_log = {timing.value for timing in EMPTY_LOG}

    whitelisted = {timing.value for timing in PHASE_START_NODES if timing.value in whitelist}
    assert whitelisted == {
        timing.value for timing in M2_PHASE_START_WHITELIST
    }, "M3 没有新解封任何阶段起点"
    for timing in PHASE_START_NODES:
        if timing in M2_PHASE_START_WHITELIST:
            continue
        assert timing.value in empty_log, f"{timing.value} 应继续留在空日志节点里"
        assert timing.value not in whitelist, f"{timing.value} 不得进 dispatch 白名单"



def test_the_dispatch_whitelist_and_the_empty_log_nodes_restate_the_implemented_set():
    assert len(WHITELIST) == 60, (
        "M2 的 53 + M3 解封的 `MOVE_AFTER`（票 07）+ wuming 票 04 的二时机"
        " + huo-taishici-xunyu 票 03 的拼点二时机 + tao-caocao 票 03 的拼点扣置时机"
        " + feng-yujin 票 02 新增的 `ROUND_START`（轮边界）"
    )
    assert len(EMPTY_LOG) == 11
    assert WHITELIST | {timing.value for timing in EMPTY_LOG} == {
        timing.value for timing in IMPLEMENTED
    }


def test_enum_only_and_empty_log_nodes_never_appear_in_any_record_stream():
    """实测判据：来源覆盖 M2 的全部行为面（回合环 / 使用 / 伤害 / 濒死 / 死亡 / 回复 /
    重洗 / 判定 / 身份 / 打出），故"一个不冒"是跑出来的，不是声明出来的。

    引擎侧**不**加白名单守卫：记录流 schema 对 84 个节点都合法（后续里程碑填位用），
    把常量硬编码进 ``Engine`` 会让后续的 dispatch 被误挡。
    """
    collected = _collected()

    assert collected.isdisjoint({timing.value for timing in ENUM_ONLY}), (
        "14 个枚举位只留位（含减体力上限二时机），一次都不 dispatch"
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
