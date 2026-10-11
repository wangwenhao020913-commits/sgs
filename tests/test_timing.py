"""时机枚举的完整性：权威表恰 83 个节点、层号派生、M2 三分区、伪时机禁令。

枚举本身是票 01 冻结数据的落地；这里只断言"表没被写错"（数量、分区、伪时机禁令、
标识符只存枚举标识），"哪些节点真的被 dispatch"由 ``tests/test_timing_coverage.py``
的覆盖清单断言。

M2 定格（票 17 §3 / 票 32）把分区改为里程碑中性名：

- ``DISPATCH_WHITELIST``（53）：真的会 dispatch、可在记录流里观测到的节点；
- ``EMPTY_LOG``（11）：已实现类里**空日志**的 L2 边界与阶段间节点——枚举位保留，
  但流程结构性地不经过、因此记录流里一个都不出现；
- ``ENUM_ONLY``（19）：枚举位保留、流程走不到。

末尾一节是**术语表计数纪律**（retro 票 06）：`GLOSSARY.md` 里这三个集合与派生的
``IMPLEMENTED`` 的四个计数必须与本文导入的长度同步，否则漂移只在 review 时被人读出来。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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

    assert len(implemented) == 65, "M2 已实现类 64 + M3 解封的 1 个（票 07）"
    assert len(enum_only) == 18, "M3 只解封 `MOVE_AFTER`，枚举位 19 → 18（票 07）"
    assert implemented | enum_only == set(Timing)
    assert implemented & enum_only == set()


def test_the_dispatch_whitelist_and_empty_log_nodes_partition_the_implemented_set():
    dispatch = set(DISPATCH_WHITELIST)
    empty_log = set(EMPTY_LOG)

    assert len(dispatch) == 54, "M2 dispatch 白名单 53 + M3 解封的 `MOVE_AFTER`（票 07）"
    assert len(empty_log) == 11, "空日志 L2 节点恰 11 个"
    assert dispatch.isdisjoint(empty_log)
    assert dispatch | empty_log == set(IMPLEMENTED), "54 + 11 复原 65 个已实现类"
    assert dispatch | empty_log | set(ENUM_ONLY) == set(Timing)


def test_the_m3_unseal_is_exactly_move_after():
    """票 07：M3 唯一解封的枚举位 = `MOVE_AFTER`，且**只解封 after**。

    配对的 `MOVE_BEFORE` 继续留在枚举位（M3 无载体，ADR-010 的不对称解封）。
    """
    assert Timing.MOVE_AFTER in IMPLEMENTED
    assert Timing.MOVE_AFTER in DISPATCH_WHITELIST
    assert Timing.MOVE_AFTER not in ENUM_ONLY
    assert Timing.MOVE_BEFORE in ENUM_ONLY, "移动前继续留枚举位（M3 无载体）"
    assert Timing.MOVE_BEFORE not in IMPLEMENTED
    assert Timing.MOVE_BEFORE not in DISPATCH_WHITELIST


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


# ---------------------------------------------------------------------------
# 术语表计数纪律：GLOSSARY.md 的时机计数必须与 enum.py 同步（retro 票 06）
# ---------------------------------------------------------------------------

GLOSSARY_PATH = Path(__file__).resolve().parent.parent / "GLOSSARY.md"
"""术语表在仓库根，是**非冻结文档**：计数漂移时就地改它，不改 ``开发文档/``（票 17 §8 R-2/R-3）。"""

_GLOSSARY_COUNT_CLAIMS: tuple[tuple[str, str], ...] = (
    ("dispatch 白名单", r"\*\*dispatch 白名单\*\*里的\s*(\d+)\s*个"),
    ("dispatch 白名单", r"观测到的\*\*时机节点\*\*集合（M2 定格\s*\*\*(\d+)\*\*\s*个）"),
    ("空日志节点", r"不出现的\s*(\d+)\s*个 L2 节点"),
    ("已实现类", r"复原\*\*已实现类\*\*的\s*(\d+)\s*个"),
    ("枚举位", r"余\s*(\d+)\s*个\*\*枚举位\*\*"),
)
"""每条 =（轴, 正则）；正则恰好捕获一个十进制计数。同一轴可以有多处声明（**每处都得对、每处都得被扫到**）。"""

_GLOSSARY_EXPECTED_COUNTS: dict[str, int] = {
    "dispatch 白名单": len(DISPATCH_WHITELIST),
    "空日志节点": len(EMPTY_LOG),
    "已实现类": len(IMPLEMENTED),
    "枚举位": len(ENUM_ONLY),
}
"""四个计数的唯一真源 = ``enum.py`` 的三个集合与由它们派生的 ``IMPLEMENTED``。"""

_GLOSSARY_FIX_HINT = "同步 GLOSSARY.md 的计数（非冻结文档，可就地改）"


def _glossary_text() -> str:
    return GLOSSARY_PATH.read_text(encoding="utf-8")


def _glossary_count_drift(text: str) -> list[str]:
    """GLOSSARY 文本里与 ``enum.py`` 计数不符的声明；空列表 = 同步。"""
    drift: list[str] = []
    for axis, pattern in _GLOSSARY_COUNT_CLAIMS:
        expected = _GLOSSARY_EXPECTED_COUNTS[axis]
        for claimed in re.findall(pattern, text):
            if int(claimed) != expected:
                drift.append(
                    f"GLOSSARY.md 的『{axis}』写 {claimed}，enum.py 是 {expected}；{_GLOSSARY_FIX_HINT}"
                )
    return drift


def _glossary_unmatched_claims(text: str) -> list[str]:
    """在文本里**一处也匹配不上**的声明正则（空 = 每条计数句都还在、正则都还有效）。"""
    return [pattern for _, pattern in _GLOSSARY_COUNT_CLAIMS if not re.search(pattern, text)]


def test_glossary_timing_counts_track_the_enum_partitions():
    """票 32 删去 ``M1_`` 前缀并把计数改成 53/64/19/11 时，GLOSSARY 是靠人读出来的。"""
    drift = _glossary_count_drift(_glossary_text())

    assert not drift, "代码里的时机计数与 GLOSSARY.md 漂移：" + "；".join(drift)


def test_the_glossary_count_scan_actually_covered_every_claim_site():
    """防"某条计数句被改写 / 正则失效 ⇒ 该声明静默掉出扫描"。

    按**声明点**判，不只按轴：同一轴的 dispatch 白名单有两处声明，只按轴判会让失效的那处
    被另一处掩盖过去。
    """
    unmatched = _glossary_unmatched_claims(_glossary_text())

    assert not unmatched, (
        "GLOSSARY.md 里这些计数声明的正则已匹配不上（措辞变了或正则失效）：" + "；".join(unmatched)
    )


def test_the_glossary_count_scan_goes_red_on_a_stale_count():
    """合成反例：把**真实 GLOSSARY** 里每条声明的数字改错一格，该轴必红。

    直接改真实文本（而不是另写一份合成模板），扫描与文档因此不会各自漂移。
    """
    text = _glossary_text()
    assert _glossary_count_drift(text) == []

    for axis, pattern in _GLOSSARY_COUNT_CLAIMS:
        match = re.search(pattern, text)
        assert match is not None, f"真实 GLOSSARY 里找不到『{axis}』的声明：{pattern}"
        claimed = match.group(1)
        stale = int(claimed) - 1
        stale_text = text[: match.start(1)] + str(stale) + text[match.end(1) :]

        drift = _glossary_count_drift(stale_text)

        assert any(axis in message for message in drift), (
            f"把『{axis}』的 {claimed} 改成 {stale} 应判红：{drift}"
        )
