"""时机枚举的完整性：权威表恰 83 个节点、层号派生、M2 三分区、伪时机禁令。

枚举本身是票 01 冻结数据的落地；这里只断言"表没被写错"（数量、分区、伪时机禁令、
标识符只存枚举标识），"哪些节点真的被 dispatch"由 ``tests/test_timing_coverage.py``
的覆盖清单断言。

M2 定格（票 17 §3 / 票 32）把分区改为里程碑中性名：

- ``DISPATCH_WHITELIST``（53）：真的会 dispatch、可在记录流里观测到的节点；
- ``EMPTY_LOG``（11）：已实现类里**空日志**的 L2 边界与阶段间节点——枚举位保留，
  但流程结构性地不经过、因此记录流里一个都不出现；
- ``ENUM_ONLY``（19）：枚举位保留、流程走不到。

此后两批解封：M3 的 ``MOVE_AFTER``（票 07）与 wuming 票 04 的
``MAXLIFE_INCREASE_BEFORE`` / ``MAXLIFE_INCREASE_AFTER``（只解封"加体力上限"一侧；
减侧继续留枚举位）。今天实测 = 白名单 **56** / 空日志 **11** / 枚举位 **16**。

末尾两节分别是 **``MAXLIFE`` 事件帧的行为**（wuming 票 04：合成技能调
``Engine.raise_hp_max`` 的三条用例）与**时机计数纪律**（retro 票 06 / code-review C7）：规则级账本
（`06` §5「程序化约定与偏离一手」的"时机四轴计数"段）里这三个集合与派生的
``IMPLEMENTED`` 的四个计数必须与本文导入的长度同步，否则漂移只在 review 时被人读出来。
术语表 `GLOSSARY.md` 只留定义、不承担账目（C7 把账目从术语表搬进账本）。

末节是**同一时机的激活结算**（`timing-activation` 票 01）：逐角色 / 一次选一条 / 选完重估 /
不升档 / 每技能一次 / 条件轮到时判 / 轮次游标不倒退 / R5 收口。它不属于"时机枚举"，
但同样以**合成技能 + ``GAME_START`` 派发点**为夹具，故与 ``MAXLIFE`` 那节同居本文件。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sgs.data import RuleConfig, load_content_pack
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierPoint,
    PriorityClass,
    need_card_tier,
)
from sgs.engine import Engine, TimingRecord, record_json
from sgs.resolution import Frame
from sgs.skills import PASS_STATE_KEY, SkillDef
from sgs.state import (
    ActivateSkill,
    DodgeContext,
    EndPhase,
    FrameKind,
    Pass,
    PeachContext,
    QueryKind,
    RespondContext,
    RespondMode,
    SelectOption,
    UseCard,
    WindowCause,
)
from sgs.timing import (
    DISPATCH_WHITELIST,
    EMPTY_LOG,
    ENUM_ONLY,
    FORBIDDEN_TIMING_NAMES,
    IMPLEMENTED,
    Timing,
)

from support import SeatSpec, build_state, cards_of_type, run_queries

PACK = load_content_pack(deck_profile="basic_53")
"""合成技能 / 构造局面用最小牌堆档（与其它构造局面单测同一档）。"""


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

    assert len(implemented) == 69, (
        "M2 已实现类 64 + M3 解封的 1 个（票 07）+ wuming 票 04 解封的 2 个"
        " + huo-taishici-xunyu 票 03 解封的拼点二时机"
    )
    assert len(enum_only) == 14, (
        "枚举位 19 − M3 的 `MOVE_AFTER` − wuming 票 04 的加体力上限二时机"
        " − 票 03 的拼点二时机"
    )
    assert implemented | enum_only == set(Timing)
    assert implemented & enum_only == set()


def test_the_dispatch_whitelist_and_empty_log_nodes_partition_the_implemented_set():
    dispatch = set(DISPATCH_WHITELIST)
    empty_log = set(EMPTY_LOG)

    assert len(dispatch) == 58, (
        "M2 dispatch 白名单 53 + M3 的 `MOVE_AFTER`（票 07）+ wuming 票 04 的二时机"
        " + 票 03 的拼点二时机"
    )
    assert len(empty_log) == 11, "空日志 L2 节点恰 11 个"
    assert dispatch.isdisjoint(empty_log)
    assert dispatch | empty_log == set(IMPLEMENTED), "58 + 11 复原 69 个已实现类"
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


def test_the_wuming_unseal_is_exactly_the_two_maxlife_increase_nodes():
    """wuming 票 04 C3 + C4：解封的恰是「加体力上限」二时机；**减侧两个继续留枚举位**。

    减侧不解封是**有意**的：既有 `reduce_hp_max`（界徐庶【潜心】/ 界吕蒙【勤学】）今天
    走自己的写入口，迁进帧会改它的 `hp_max_change.frame_id` 并多两条时机记录 ⇒ 金录像
    字节变化（E1）。加 / 减是规则 §4.15 的两个独立流程，各只有「前 / 后」。
    """
    for timing in (Timing.MAXLIFE_INCREASE_BEFORE, Timing.MAXLIFE_INCREASE_AFTER):
        assert timing in IMPLEMENTED, "解封 = 引擎真的会 dispatch 它（由 MAXLIFE 帧兑现）"
        assert timing in DISPATCH_WHITELIST
        assert timing not in ENUM_ONLY
    for timing in (Timing.MAXLIFE_DECREASE_BEFORE, Timing.MAXLIFE_DECREASE_AFTER):
        assert timing in ENUM_ONLY, "减侧暂不解封（不迁 reduce_hp_max，见票 04 C4）"
        assert timing not in IMPLEMENTED
        assert timing not in DISPATCH_WHITELIST


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
# L11 ``MAXLIFE`` 事件帧（wuming 票 04 C1–C5）：合成技能调 ``Engine.raise_hp_max``
#
# 规则 §4.15 L429–L439：加体力上限 / 减体力上限是**两个独立流程**，各只有「前 / 后」。
# 本票只接"加"：`raise_hp_max` 推 ``FrameKind.MAXLIFE`` 帧，帧私有带 ``direction`` +
# ``amount``；"减"分支写好但暂无调用者（既有 `reduce_hp_max` 不迁、`MAXLIFE_DECREASE_*`
# 不解封）。这些用例用 ``CATALOG`` 注入的合成锁定技钉形状——不引入任何真实武将。
# ---------------------------------------------------------------------------

MAXLIFE_SKILL = "hero.fake.maxlife"
"""合成 L11 技能的 id（只活在 ``CATALOG`` 注入里）。"""

_MAXLIFE_RAISED = "fake_maxlife_raised"
"""帧私有守卫：这次 L0 派发已经推过 MAXLIFE 帧。派发环在别的实例挂起后重入时会从头
再跑一遍已完成的钩子（``_dispatch`` 的既有重入口径），没有它就会再推一个帧。"""


def _install_maxlife_skill(monkeypatch, *, amount: int):
    """把一条**只挂在 ``GAME_START`` 上**的合成锁定技注入 ``CATALOG``（票 04 的 DoD 夹具）。

    锁定技 ⇒ 不发"是否发动"询问，回调直接跑；它只做一件事：按 ``hook_owner``
    （帧私有的持有者槽）调 ``Engine.raise_hp_max``，然后 ``return "stay"`` 让引擎先跑完
    这个帧。效果不重复由帧私有守卫保证（``CODING_STANDARDS.md`` §3）。
    """
    from types import MappingProxyType

    from sgs.effects import CarrierKind
    from sgs.skills import Hook, SkillDef
    from sgs.skills import catalog as catalog_module

    def handler(host, frame):
        seat = int(frame.data["hook_owner"])
        if frame.data.get(_MAXLIFE_RAISED):
            return None
        frame.data[_MAXLIFE_RAISED] = True
        host.raise_hp_max(seat, amount)
        return "stay"

    definition = SkillDef(
        skill_id=MAXLIFE_SKILL,
        locked=True,
        carrier=CarrierKind.HERO,
        hooks=(Hook(Timing.GAME_START, handler),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, MAXLIFE_SKILL: definition}),
    )


def _step_until_the_maxlife_frame_closes(engine) -> None:
    """推进到 ``MAXLIFE`` 帧 pop（合成锁定技不发问，故一路 ``_step`` 即可）。"""
    for _ in range(50):
        if [
            record
            for record in engine.records
            if record.kind == "frame"
            and record.frame_kind is FrameKind.MAXLIFE
            and record.op == "pop"
        ]:
            return
        assert engine.pending is None, "合成锁定技不该发问"
        engine._step()
    raise AssertionError("50 步内 MAXLIFE 帧没有跑完")


def _records_of(engine, *, kind: str) -> list:
    return [record for record in engine.records if record.kind == kind]


def _maxlife_frame_id(engine) -> int:
    pushes = [
        record
        for record in _records_of(engine, kind="frame")
        if record.frame_kind is FrameKind.MAXLIFE and record.op == "push"
    ]
    assert len(pushes) == 1, "一次 raise_hp_max = 恰好一个 MAXLIFE 帧"
    return pushes[0].frame_id


def test_raise_hp_max_pushes_a_maxlife_frame_and_never_syncs_the_hp(monkeypatch):
    """C1 + C2 + C5：合成技能调 ``raise_hp_max(+1)`` ⇒

    - 帧类型 ``MAXLIFE``（push / pop 各一条，生命周期在记录流里没有缺口）；
    - ``hp_max_change`` 的 ``frame_id`` 指向该帧，``amount`` = 1、``before/after`` = 4/5；
    - 两个「加」时机各记录一次，序为 ① 前 → ② 落上限 → ③ 后；减侧一条都不落；
    - **体力值不变**：满体力加上限也不触发"同步下调"（那是减侧的规则），也没有 ``hp_change``。
    """
    _install_maxlife_skill(monkeypatch, amount=1)
    engine = Engine.new_game(seed=7, rule_config=RuleConfig(player_count=2), pack=PACK)
    engine.grant_skill(0, MAXLIFE_SKILL)
    _step_until_the_maxlife_frame_closes(engine)

    player = engine.state.player(0)
    assert (player.hp, player.hp_max) == (4, 5), "上限 +1；体力值不同步（回复是技能的第二段）"

    frame_id = _maxlife_frame_id(engine)
    assert [
        record.frame_id
        for record in _records_of(engine, kind="frame")
        if record.frame_kind is FrameKind.MAXLIFE and record.op == "pop"
    ] == [frame_id], "pop 必须与 push 同一个帧号"

    timings = [
        record
        for record in _records_of(engine, kind="timing")
        if record.timing.value.startswith("MAXLIFE_")
    ]
    assert [record.timing for record in timings] == [
        Timing.MAXLIFE_INCREASE_BEFORE,
        Timing.MAXLIFE_INCREASE_AFTER,
    ], "两个「加体力上限」时机各一次；减侧两个时机一条都不落（不解封）"
    assert [record.frame_id for record in timings] == [frame_id, frame_id]
    assert [record.actor for record in timings] == [0, 0], "时机记录的主体 = 被改上限的座位"
    assert [record.targets for record in timings] == [(0,), (0,)], "帧私有 targets = 该座位"

    deltas = [
        record
        for record in _records_of(engine, kind="delta")
        if record.delta_type == "hp_max_change"
    ]
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.frame_id == frame_id, "hp_max_change 指向承载它的 MAXLIFE 帧"
    assert (delta.seat, delta.before, delta.after, delta.amount) == (0, 4, 5, 1)
    assert timings[0].seq < delta.seq < timings[1].seq, "① 派发前时机 → ② 落上限 → ③ 派发后时机"
    assert not [
        record
        for record in _records_of(engine, kind="delta")
        if record.delta_type == "hp_change"
    ], "满体力加上限**不**同步体力（「满体力同步下调」只属于减侧）"


def test_raise_hp_max_carries_the_positive_magnitude_and_does_not_touch_the_hp(monkeypatch):
    """C2 的幅度语义：``raise_hp_max(+3)`` ⇒ ``hp_max_change.amount == 3``（幅度恒正）、上限 +3。

    已受伤时更不会动体力——"增加上限并回复 1 点"的回复是技能的第二段，走既有回复原语
    （杜预【灭吴】一类将来直接用这个入口）。
    """
    _install_maxlife_skill(monkeypatch, amount=3)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=2, hp_max=3), SeatSpec()],
        deck=tuple(range(10)),
    )
    engine = Engine.from_state(state, seed=7)
    engine.grant_skill(0, MAXLIFE_SKILL)
    _step_until_the_maxlife_frame_closes(engine)

    player = engine.state.player(0)
    assert (player.hp, player.hp_max) == (2, 6), "上限 3 → 6；体力值不动"
    deltas = [
        record
        for record in _records_of(engine, kind="delta")
        if record.delta_type == "hp_max_change"
    ]
    assert [(record.before, record.after, record.amount) for record in deltas] == [(3, 6, 3)]


def test_reduce_hp_max_stays_out_of_the_maxlife_frame():
    """C4 的边界：减上限仍走**自己的**写入口（界徐庶【潜心】/ 界吕蒙【勤学】在用）。

    调用它不推 ``MAXLIFE`` 帧、不落任何 ``MAXLIFE_*`` 时机记录，且"减上限前满体力 ⇒
    体力值同步下调到新上限"（规则 L436–437）照旧落第二条 ``hp_change``。
    既有觉醒技的记录流因此逐字节不变（字节级判据 = 金录像 57 / 59 的回放比对）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hp_max=4), SeatSpec()],
        deck=tuple(range(10)),
    )
    engine = Engine.from_state(state, seed=7)
    carrier = Frame(kind=FrameKind.SKILL, frame_id=0)

    engine.reduce_hp_max(carrier, 0, 1)

    player = engine.state.player(0)
    assert (player.hp, player.hp_max) == (3, 3), "满体力减上限 ⇒ 体力同步下调（减侧的规则）"
    assert not [
        record
        for record in _records_of(engine, kind="frame")
        if record.frame_kind is FrameKind.MAXLIFE
    ], "reduce_hp_max 不推 MAXLIFE 帧"
    assert not [
        record
        for record in _records_of(engine, kind="timing")
        if record.timing.value.startswith("MAXLIFE_")
    ], "减侧时机不解封：一条记录都不许落"
    assert [
        (record.delta_type, record.before, record.after, record.amount)
        for record in _records_of(engine, kind="delta")
    ] == [("hp_max_change", 4, 3, 1), ("hp_change", 4, 3, -1)]


# ---------------------------------------------------------------------------
# 时机计数纪律：规则级账本的"时机四轴计数"段必须与 enum.py 同步（retro 票 06 / C7）
# ---------------------------------------------------------------------------

MILESTONES_PATH = (
    Path(__file__).resolve().parent.parent / "开发文档" / "06-里程碑与风险.md"
)
"""计数账本 = `06` §5「程序化约定与偏离一手」的"时机四轴计数"段（code-review C7）。

术语表 `GLOSSARY.md` 只留定义、**不承担账目**；四轴计数只住账本一处，代码与账本必须同一次
改动（票 32 以来的计数纪律）。扫的是**规则级账本段**而不是术语表：定义与账目分家，
"54 = 53 + 1"那类自相矛盾的实现账目因此不会再落进术语表。
"""

LEDGER_SECTION_HEADING = "## 5. 程序化约定与偏离一手"
"""计数段所在小节；对账前先确认它还在（小节被挪号 / 改标题 ⇒ 扫描目标错位即判红）。"""

LEDGER_PARAGRAPH_MARK = "**时机四轴计数（M3 收口；`tests/test_timing.py` 与本段对账）**"
"""计数段的段首标记（覆盖元断言据此确认我们扫的是账本段、不是别的计数句）。"""

_LEDGER_COUNT_CLAIMS: tuple[tuple[str, str], ...] = (
    ("dispatch 白名单", r"`dispatch 白名单`\s*\*\*(\d+)\*\*\s*个"),
    ("空日志节点", r"`空日志节点`\s*\*\*(\d+)\*\*\s*个"),
    ("已实现类", r"`已实现类`\s*\*\*(\d+)\*\*\s*个"),
    ("枚举位", r"余\s*\*\*(\d+)\*\*\s*个\s*`枚举位`"),
)
"""每条 =（轴, 正则）；正则恰好捕获一个十进制计数。四轴各一处声明。"""

_LEDGER_EXPECTED_COUNTS: dict[str, int] = {
    "dispatch 白名单": len(DISPATCH_WHITELIST),
    "空日志节点": len(EMPTY_LOG),
    "已实现类": len(IMPLEMENTED),
    "枚举位": len(ENUM_ONLY),
}
"""四个计数的唯一真源 = `enum.py` 的三个集合与由它们派生的 ``IMPLEMENTED``。"""

_LEDGER_FIX_HINT = "同步规则级账本的『时机四轴计数』段（非冻结文档，可就地改）"


def _ledger_text() -> str:
    return MILESTONES_PATH.read_text(encoding="utf-8")


def _ledger_count_drift(text: str) -> list[str]:
    """账本文本里与 ``enum.py`` 计数不符的声明；空列表 = 同步。"""
    drift: list[str] = []
    for axis, pattern in _LEDGER_COUNT_CLAIMS:
        expected = _LEDGER_EXPECTED_COUNTS[axis]
        for claimed in re.findall(pattern, text):
            if int(claimed) != expected:
                drift.append(
                    f"规则级账本的『{axis}』写 {claimed}，enum.py 是 {expected}；"
                    f"{_LEDGER_FIX_HINT}"
                )
    return drift


def _ledger_unmatched_claims(text: str) -> list[str]:
    """在文本里**一处也匹配不上**的声明正则（空 = 每条计数句都还在、正则都还有效）。"""
    return [pattern for _, pattern in _LEDGER_COUNT_CLAIMS if not re.search(pattern, text)]


def test_glossary_timing_counts_track_the_enum_partitions():
    """四轴计数与 `enum.py` 同步（计数账本自 C7 起住规则级账本、不住术语表）。"""
    text = _ledger_text()
    assert LEDGER_SECTION_HEADING in text, "计数段所在小节被挪号 / 改标题"
    assert LEDGER_PARAGRAPH_MARK in text, "账本里找不到『时机四轴计数』段"

    drift = _ledger_count_drift(text)

    assert not drift, "代码里的时机计数与规则级账本漂移：" + "；".join(drift)


def test_the_glossary_count_scan_actually_covered_every_claim_site():
    """防"某条计数句被改写 / 正则失效 ⇒ 该声明静默掉出扫描"。

    按**声明点**判：每条声明的正则都必须在真实账本段里命中一次——否则空转即判红。
    """
    unmatched = _ledger_unmatched_claims(_ledger_text())

    assert not unmatched, (
        "规则级账本里这些计数声明的正则已匹配不上（措辞变了或正则失效）："
        + "；".join(unmatched)
    )


def test_the_glossary_count_scan_goes_red_on_a_stale_count():
    """合成反例：把**真实账本**里每条声明的数字改错一格，该轴必红。

    直接改真实文本（而不是另写一份合成模板），扫描与文档因此不会各自漂移。
    """
    text = _ledger_text()
    assert _ledger_count_drift(text) == []

    for axis, pattern in _LEDGER_COUNT_CLAIMS:
        match = re.search(pattern, text)
        assert match is not None, f"真实账本里找不到『{axis}』的声明：{pattern}"
        claimed = match.group(1)
        stale = int(claimed) - 1
        stale_text = text[: match.start(1)] + str(stale) + text[match.end(1) :]

        drift = _ledger_count_drift(stale_text)

        assert any(axis in message for message in drift), (
            f"把『{axis}』的 {claimed} 改成 {stale} 应判红：{drift}"
        )


# ---------------------------------------------------------------------------
# 同一时机的激活结算（票 01）：逐角色 / 一次选一条 / 选完重估 / 不升档 / 每技能一次
#
# 规则出处：仓内 §4.16 L443–L466（同档内次序归玩家）+ 补充一手《三国杀规则集 V3.0》
# §2.1.2.2(2)（一次一个 / 每技能一次 / 条件轮到时判 / 终止即收口）与 §2.1.2.3（不升档）。
# 全部用 ``CATALOG`` 注入的**合成技能**钉形状——不依赖任何真实武将，故真实对局的记录流
# 不受这些用例影响；"候选数为 1 的时机逐字节不变"由 ``tools/check_goldens.sh`` 单独判
# （47 条录像 ≈ 40 MiB，不在单测里重读，§8-9）。
# ---------------------------------------------------------------------------

FAKE_A = "hero.fake.face_a"
FAKE_B = "hero.fake.face_b"
FAKE_EQUIP = "hero.fake.face_equip"
"""合成技能 id（只活在 ``CATALOG`` 注入里）：同档内按 id 的字典序 ⇒ A 先于 B。"""


def _install_fake_skills(monkeypatch, *definitions) -> None:
    """把合成技能注入 ``CATALOG``（票 01 的夹具；与 ``MAXLIFE`` 那节同姿态）。"""
    from types import MappingProxyType

    from sgs.skills import catalog as catalog_module

    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType(
            {
                **catalog_module.CATALOG,
                **{definition.skill_id: definition for definition in definitions},
            }
        ),
    )


def _fake_skill(skill_id, *, handler=None, flow=None, condition=None, carrier=None):
    """一条**只挂在 ``GAME_START`` 上**的合成非锁定技（可带条件 / 流水线 / 载体）。"""
    from sgs.effects import CarrierKind
    from sgs.skills import Hook, SkillDef

    return SkillDef(
        skill_id=skill_id,
        locked=False,
        carrier=CarrierKind.HERO if carrier is None else carrier,
        hooks=(
            Hook(Timing.GAME_START, handler=handler, condition=condition, flow=flow),
        ),
    )


def _activation_engine(*granted: str) -> Engine:
    """两人局、无武将；把 ``granted`` 授予座位 0（L0 之前授予 ⇒ GAME_START 派发得到）。"""
    engine = Engine.new_game(seed=7, rule_config=RuleConfig(player_count=2), pack=PACK)
    for skill_id in granted:
        engine.grant_skill(0, skill_id)
    return engine


class _FaceScript:
    """按"每问选哪一条"应答的脚本参与者；顺带记下每一问的**候选技能序列**。

    ``picks`` 逐问消费：``None`` = 答"放弃"。非 ``SKILL_ACTIVATION`` 的询问（合成流水线
    里的选项步）取第一个非放弃候选；问到的技能不在本问面上时同样退到放弃，让断言在更靠前
    的地方变红，而不是抛 KeyError。
    """

    def __init__(self, *picks: str | None) -> None:
        self.picks = list(picks)
        self.faces: list[tuple[str, ...]] = []

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION:
            face = tuple(
                action.skill_id
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
            self.faces.append(face)
            wanted = self.picks.pop(0) if self.picks else None
            if wanted is not None:
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill) and action.skill_id == wanted:
                        return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
            return query.legal_actions[0]
        for action in query.legal_actions:
            if not isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _activation_queries(engine) -> list:
    """记录流里的全部 ``SKILL_ACTIVATION`` 询问。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SKILL_ACTIVATION
    ]


def _face_of(query) -> tuple[str, ...]:
    """一询问的候选技能序列（**次序本身**，不排序、不去重：`CODING_STANDARDS.md` §4）。"""
    return tuple(
        action.skill_id
        for action in query.legal_actions
        if isinstance(action, ActivateSkill)
    )


def _drive(engine, script: _FaceScript, stop, *, limit: int = 400) -> None:
    """推进引擎到 ``stop(engine)`` 成立；每个挂起询问交给 ``script``。"""
    for _ in range(limit):
        if stop(engine):
            return
        if engine.pending is None:
            engine._step()
            continue
        engine.submit(script.respond(engine.pending))
    raise AssertionError("步数上限内没有到达停止条件")


def test_a_single_candidate_face_keeps_the_frozen_query_shape(monkeypatch):
    """§8-1（Q6=B）：只有一个候选时，询问面与**序列化字段集**与改造前逐字节相同。

    判据钉三件事：① 候选恰是 ``[ActivateSkill, Pass]``、``skill_id`` 就是那一条；
    ② 询问记录里的字段集恰是**冻结报文**那一组——``skill_ids`` /
    ``activation_preactivations`` 是本地面（``exclude=True``），不进记录流；③ 同一时机
    只落一条询问（放弃即收口）。真实 47 条录像的逐字节比对是 ``tools/check_goldens.sh``。
    """
    ran: list[str] = []
    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, handler=lambda host, frame: ran.append(FAKE_A)),
    )
    engine = _activation_engine(FAKE_A)
    script = _FaceScript(None)  # 放弃
    _drive(engine, script, lambda e: bool(_activation_queries(e)))

    query = _activation_queries(engine)[0]
    assert query.skill_id == FAKE_A
    assert query.optional is True
    assert (query.min_picks, query.max_picks, query.slots) == (0, 0, ()), (
        "冻结报文（A-4 待清理）在单候选面上一个字不改"
    )
    assert query.legal_actions == (
        ActivateSkill(skill_id=FAKE_A, answers_seq=query.seq),
        Pass(answers_seq=query.seq),
    ), "单候选面的候选与改造前逐条相同（一条裸 ActivateSkill + 可放弃）"
    assert query.skill_ids == (FAKE_A,), "本地面照常装本轮候选（只是不进记录流）"
    assert query.activation_preactivations == {}, "未翻预发动的技能没有清单"

    record = next(
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SKILL_ACTIVATION
    )
    serialized = json.loads(record_json(record))["query"]
    assert set(serialized) == {
        "kind",
        "seq",
        "actor",
        "timing",
        "prompt",
        "legal_actions",
        "view",
        "skill_id",
        "optional",
        "min_picks",
        "max_picks",
        "slots",
    }, "序列化面只许是冻结报文那一组：本轮候选清单是本地面（exclude=True）"

    engine.submit(script.respond(engine.pending))
    _drive(
        engine,
        script,
        lambda e: e.pending is not None and e.pending.kind != QueryKind.SKILL_ACTIVATION,
    )
    assert len(_activation_queries(engine)) == 1, "放弃 = 结束本座位的这个时机，不重问"
    assert ran == [], "放弃 ⇒ 不执行"


def test_two_same_tier_skills_narrow_the_face_one_pick_at_a_time(monkeypatch):
    """§8-2：两条同档技能 ⇒ 一次询问里两条候选都在；选 A 后只剩 B；选 B 后收口。

    断的是**询问序列本身**（`CODING_STANDARDS.md` §4：不排序、不去重）。
    """
    ran: list[str] = []

    def record(skill_id):
        def handler(host, frame):
            del host, frame
            ran.append(skill_id)
            return None

        return handler

    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, handler=record(FAKE_A)),
        _fake_skill(FAKE_B, handler=record(FAKE_B)),
    )
    engine = _activation_engine(FAKE_A, FAKE_B)
    script = _FaceScript(FAKE_A, FAKE_B)

    _drive(engine, script, lambda e: ran == [FAKE_A, FAKE_B])

    assert script.faces == [(FAKE_A, FAKE_B), (FAKE_B,)], (
        "第一问是**一张面两条候选**（同档内次序归玩家），选 A 之后重估只剩 B："
        f"实得 {script.faces}"
    )
    assert ran == [FAKE_A, FAKE_B], "选中的那一条按询问序各跑一次"
    assert len(_activation_queries(engine)) == 2, "一轮一询问：两条候选不是两条询问"


def test_a_lower_tier_pick_closes_the_higher_tier_for_the_rest_of_the_pass(monkeypatch):
    """§8-3（不升档）：「武将 + 装备」两条，先取**装备**档 ⇒ 武将条不再进候选。

    形状 = 补充一手 §2.1.2.3 的曹操/八卦阵⇒护驾：首轮两档都上脸（同档内次序归玩家），
    选定装备档之后 ``pass_floor = 1`` ⇒ 武将档（0）被闸门挡掉，本 pass 内不再出现。
    """
    from sgs.effects import CarrierKind

    ran: list[str] = []
    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, handler=lambda host, frame: ran.append(FAKE_A)),
        _fake_skill(
            FAKE_EQUIP,
            handler=lambda host, frame: ran.append(FAKE_EQUIP),
            carrier=CarrierKind.EQUIPMENT,
        ),
    )
    engine = _activation_engine(FAKE_A, FAKE_EQUIP)
    script = _FaceScript(FAKE_EQUIP)  # 先取低档（装备）

    _drive(engine, script, lambda e: ran == [FAKE_EQUIP])

    assert script.faces == [(FAKE_A, FAKE_EQUIP)], (
        "首轮两档都要上脸（玩家自己决定先发动哪一档）；选了装备档之后武将档不再进面："
        f"实得 {script.faces}"
    )
    assert FAKE_A not in ran, "被闸门挡掉的那条一次都不跑"
    assert len(_activation_queries(engine)) == 1, "没有第二条面：剩下的候选被闸门挡掉"


def test_a_skill_is_offered_at_most_once_per_seat_pass(monkeypatch):
    """§8-4（每技能一次）：A 的条件恒真，也不会在同一个 pass 里二次上脸。

    选 A ⇒ 下一轮虽然 A 的条件仍成立、仍然"可发动"，但 ``pass_used`` 记过 ⇒ 只剩 B。
    """
    ran: list[str] = []
    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, handler=lambda host, frame: ran.append(FAKE_A)),
        _fake_skill(FAKE_B, handler=lambda host, frame: ran.append(FAKE_B)),
    )
    engine = _activation_engine(FAKE_A, FAKE_B)
    script = _FaceScript(FAKE_A, None)  # 选 A，然后对 B 答放弃

    _drive(engine, script, lambda e: ran == [FAKE_A])

    assert script.faces == [(FAKE_A, FAKE_B)], (
        "A 已选过 ⇒ 第二问只剩 B（A 不再上脸）：" f"实得第一问 {script.faces}"
    )
    assert _face_of(engine.pending) == (FAKE_B,), (
        f"同一个 pass 里 A 不会二次上脸；实得第二问 {_face_of(engine.pending)}"
    )
    offered = [skill_id for face in script.faces for skill_id in face]
    assert offered.count(FAKE_A) == 1, "同一条技能在同一个 pass 里不会二次上脸"


def test_a_condition_is_judged_when_the_next_round_starts(monkeypatch):
    """§8-5（条件轮到时判）：A 的效果改变 B 的条件 ⇒ B 在**下一轮**才消失 / 出现。

    两个方向都断：① 首轮 B 在面上（条件此刻成立），A 的效果把它关掉 ⇒ 下一轮不再上脸；
    ② 首轮 B 不在（条件不成立），A 的效果把它打开 ⇒ 下一轮才出现。
    """
    # 方向 ①：A 把 B 的条件关掉。
    flags = {"b": True}
    ran: list[str] = []

    def close_b(host, frame):
        del host, frame
        flags["b"] = False
        ran.append(FAKE_A)
        return None

    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, handler=close_b),
        _fake_skill(
            FAKE_B,
            handler=lambda host, frame: ran.append(FAKE_B),
            condition=lambda host, frame: flags["b"],
        ),
    )
    engine = _activation_engine(FAKE_A, FAKE_B)
    script = _FaceScript(FAKE_A)
    _drive(engine, script, lambda e: ran == [FAKE_A])

    assert script.faces == [(FAKE_A, FAKE_B)], (
        "首轮按当时条件判：B 的条件成立 ⇒ 在面上；A 的效果关掉它之后，下一轮重判 ⇒ 不再上脸："
        f"实得 {script.faces}"
    )
    assert len(_activation_queries(engine)) == 1

    # 方向 ②：A 把 B 的条件打开。
    flags2 = {"b": False}
    ran2: list[str] = []

    def open_b(host, frame):
        del host, frame
        flags2["b"] = True
        ran2.append(FAKE_A)
        return None

    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, handler=open_b),
        _fake_skill(
            FAKE_B,
            handler=lambda host, frame: ran2.append(FAKE_B),
            condition=lambda host, frame: flags2["b"],
        ),
    )
    engine2 = _activation_engine(FAKE_A, FAKE_B)
    script2 = _FaceScript(FAKE_A, FAKE_B)
    _drive(engine2, script2, lambda e: ran2 == [FAKE_A, FAKE_B])

    assert script2.faces == [(FAKE_A,), (FAKE_B,)], (
        "B 的条件在 A 结算前不成立 ⇒ 首轮不上脸；A 打开之后下一轮才出现："
        f"实得 {script2.faces}"
    )


def test_a_skill_granted_mid_pass_joins_the_next_round(monkeypatch):
    """§8-6（Q5=A 每轮重解析）：A 的效果里 ``grant_skill`` 得到的新实例本轮即可发动。"""

    def grant_b(host, frame):
        host.grant_skill(0, FAKE_B, frame=frame)
        ran.append(FAKE_A)
        return None

    ran: list[str] = []
    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, handler=grant_b),
        _fake_skill(FAKE_B, handler=lambda host, frame: ran.append(FAKE_B)),
    )
    # 只授予 A：B 的**声明**在目录里，但实例要等 A 的效果授予才出现。
    engine = _activation_engine(FAKE_A)
    script = _FaceScript(FAKE_A, FAKE_B)

    _drive(engine, script, lambda e: ran == [FAKE_A, FAKE_B])

    assert script.faces == [(FAKE_A,), (FAKE_B,)], (
        "B 在首轮收集时还不是实例；A 结算后每轮重解析 ⇒ 下一轮进面："
        f"实得 {script.faces}"
    )


def test_a_suspended_effect_does_not_reask_or_rewind_the_round(monkeypatch):
    """§8-7（悬起 / 重入）：A 的效果挂起（问一次选项）⇒ 不重发询问、``pass_round`` 不倒退。

    判据读三处**都是真产出路径**：询问记录（重问会多一条）、帧私有的 pass 游标
    （``pass_round`` / ``pass_chosen``，经 ``Engine.phase_body_frame`` 读）、以及 A 的效果体
    被执行了几次。
    """
    from sgs.skills import PickOption, SkillFlow

    ran: list[str] = []

    def effect(host, frame, answers):
        del host, frame, answers
        ran.append(FAKE_A)
        return None

    flow_a = SkillFlow(
        name="fake_suspended_a",
        steps=(PickOption(options=("go",), key="go"),),
        effect=effect,
    )
    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, flow=flow_a),
        _fake_skill(FAKE_B, handler=lambda host, frame: ran.append(FAKE_B)),
    )
    engine = _activation_engine(FAKE_A, FAKE_B)
    script = _FaceScript(FAKE_A, FAKE_B)

    # ① 首问：一张面两条候选 ⇒ 选 A。
    _drive(engine, script, lambda e: bool(_activation_queries(e)))
    engine.submit(script.respond(engine.pending))

    # ② A 的流水线挂起在选项步：游标仍是"本轮进行中"（第 0 轮、选中 A），不倒退。
    _drive(
        engine,
        script,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.SELECT_OPTION,
    )
    cursor = _pass_cursor(engine)
    assert cursor["pass_round"] == 0, "A 还没跑完 ⇒ 轮次停在 0"
    assert cursor["pass_chosen"] is not None
    assert cursor["pass_chosen"].skill_id == FAKE_A, "悬起的是选中项本身"
    assert len(_activation_queries(engine)) == 1, "挂起期间不重发询问"

    # ③ 答完选项 ⇒ A 跑完、轮次前进；第二问只剩 B（重入不重问、不重跑已完成项）。
    engine.submit(script.respond(engine.pending))
    _drive(engine, script, lambda e: len(_activation_queries(e)) == 2)

    cursor = _pass_cursor(engine)
    assert cursor["pass_round"] == 1, "已结算一轮：pass_round 前进且不倒退"
    assert cursor["pass_used"] == (FAKE_A,), "跑过的那一条落进 pass_used"
    assert script.faces == [(FAKE_A, FAKE_B)], (
        f"重入不重发询问、不重跑已完成项；实得 {script.faces}"
    )
    assert _face_of(engine.pending) == (FAKE_B,), (
        f"第二问只报剩下的 B（A 不重问）；实得 {_face_of(engine.pending)}"
    )

    engine.submit(script.respond(engine.pending))
    _drive(engine, script, lambda e: ran == [FAKE_A, FAKE_B])
    assert ran.count(FAKE_A) == 1, "A 的效果体恰好跑一次（悬起续跑不重跑）"
    assert ran == [FAKE_A, FAKE_B]


def test_an_effect_can_end_the_pass_explicitly(monkeypatch):
    """R5（§4-7）：先发动者的效果终止此时机 ⇒ 其余候选作废（本票只给判据位 + 这条单测）。

    规则出处 = 补充一手 §2.1.2.2(2) 的注意条。判据 = 第二条技能既不再上脸、也不再执行。
    """
    from sgs.skills import terminate_activation_pass

    ran: list[str] = []

    def end_pass(host, frame):
        del host
        terminate_activation_pass(
            frame, timing=Timing.GAME_START, key=Timing.GAME_START.value
        )
        ran.append(FAKE_A)
        return None

    _install_fake_skills(
        monkeypatch,
        _fake_skill(FAKE_A, handler=end_pass),
        _fake_skill(FAKE_B, handler=lambda host, frame: ran.append(FAKE_B)),
    )
    engine = _activation_engine(FAKE_A, FAKE_B)
    script = _FaceScript(FAKE_A, FAKE_B)

    _drive(engine, script, lambda e: bool(ran))
    _drive(
        engine,
        script,
        lambda e: e.pending is not None and e.pending.kind != QueryKind.SKILL_ACTIVATION,
    )

    assert script.faces == [(FAKE_A, FAKE_B)], "收口之后不再发第二问"
    assert ran == [FAKE_A], "终止即收口：其余候选作废"


def test_a_forced_segment_runs_before_the_optional_face_without_asking(monkeypatch):
    """§4-5：强制段（锁定技）**不问、不上脸**，但轮到本档时直接跑（同档内先于可选项）。

    链序里 ``hero.fake.face_a`` 在 ``hero.fake.forced`` 之前（按 id），故"强制段先跑"只能
    来自本票的那条程序化约定，不是排序碰巧。
    """
    from sgs.effects import CarrierKind
    from sgs.skills import Hook, SkillDef

    order: list[str] = []

    def forced_handler(host, frame):
        del host, frame
        order.append("forced")
        return None

    forced = SkillDef(
        skill_id="hero.fake.forced",
        locked=True,
        carrier=CarrierKind.HERO,
        hooks=(Hook(Timing.GAME_START, forced_handler),),
    )
    _install_fake_skills(
        monkeypatch,
        forced,
        _fake_skill(FAKE_A, handler=lambda host, frame: order.append("optional")),
    )
    engine = _activation_engine("hero.fake.forced", FAKE_A)
    script = _FaceScript(FAKE_A)

    _drive(engine, script, lambda e: order == ["forced", "optional"])

    assert script.faces == [(FAKE_A,)], "锁定技不上脸：面里只有可选项"
    assert order == ["forced", "optional"], "强制段先跑，然后才问可选项"
    assert len(_activation_queries(engine)) == 1


def test_the_seam_path_carries_tiers_and_the_same_gate(monkeypatch):
    """§6：``dispatch_seam`` 与 ``dispatch_timing`` 共用 ``_dispatch`` ⇒ 接缝候选同样带档、
    同样过"不升档 / 每技能一次"的闸门（不是只给钩子那条路改）。

    判据 = 接缝面上两档都在；选了装备档之后武将档被闸门挡掉、不再发第二问。
    """
    from sgs.effects import CarrierKind
    from sgs.skills import Seam, SeamKind, SkillDef

    ran: list[str] = []

    def seam_of(skill_id, carrier, log):
        return SkillDef(
            skill_id=skill_id,
            locked=False,
            carrier=carrier,
            seams=(
                Seam(
                    SeamKind.EQUIPMENT_DISCARD,
                    handler=lambda host, frame: log.append(skill_id),
                ),
            ),
        )

    _install_fake_skills(
        monkeypatch,
        seam_of(FAKE_A, CarrierKind.HERO, ran),
        seam_of(FAKE_EQUIP, CarrierKind.EQUIPMENT, ran),
    )
    engine = _activation_engine(FAKE_A, FAKE_EQUIP)
    frame = Frame(kind=FrameKind.RESOLVE, data={"source": 0}, frame_id=101)

    assert engine.dispatch_seam(frame, SeamKind.EQUIPMENT_DISCARD) == "stay"
    query = engine.pending
    assert _face_of(query) == (FAKE_A, FAKE_EQUIP), (
        f"接缝面同样两档都在（同一张面）；实得 {_face_of(query)}"
    )
    engine.submit(ActivateSkill(skill_id=FAKE_EQUIP, answers_seq=query.seq))

    assert engine.dispatch_seam(frame, SeamKind.EQUIPMENT_DISCARD) is None
    assert ran == [FAKE_EQUIP], "选了装备档 ⇒ 武将档被闸门挡掉（接缝同样不升档）"
    assert len(_activation_queries(engine)) == 1


def _pass_cursor(engine) -> dict:
    """当前 ``GAME_START`` 不压栈帧里的 pass 游标（帧私有的唯一读法 = 公开访问器）。"""
    frame = engine.phase_body_frame()
    assert frame is not None, "GAME_START 的派发帧应当还在（挂起在没有出栈的帧上）"
    states = frame.data.get(PASS_STATE_KEY) or {}
    assert len(states) == 1, f"本用例里同一时机只该有一趟 pass：{states}"
    return next(iter(states.values()))



# ---------------------------------------------------------------------------
# 需要（使用 / 打出）牌窗口的档位闸门（票 03 §4–§5）
#
# 规则出处：补充一手《三国杀规则集 V3.0》§4.2.2 / §4.2.5（「需要牌」拆成 ❶❷❸ 三个固定
# 顺序的时机）+ §2.1.2.3（**不升档**：曹操先发动【八卦阵】⇒ 判定黑 ⇒「只能使用一张牌名为
# 【闪】的牌，没有发动〖护驾〗的时机了」）。本票只给 **❶**（提供者窗口）补档位与闸门；
# ❷/❸ 的合并面按「结果等价」保留并登记偏离（票面 §4′）。
#
# 本节的提供者是**合成技能**（不新增武将 / 装备数据）：档由载体声明（``HERO`` / ``EQUIPMENT``），
# 两条都只在需要【闪】的窗口里上脸。不注册提供者回调 ⇒ 引擎走"选中才惰性判定"那条路
# （与【八卦阵】同形：红 = 需要被满足，黑 = 没满足、窗口重问）。
# ---------------------------------------------------------------------------

NEED_HERO = "hero.fake.need_hero"
NEED_EQUIP = "hero.fake.need_equip"
"""合成提供者 id（只活在 ``CATALOG`` 注入里）：武将档 / 装备档各一条。"""


def _need_card_provider(skill_id: str, *, carrier: CarrierKind) -> SkillDef:
    """一条合成**提供者**：候选 = 自己的裸 ``ActivateSkill``（只在【闪】窗口）。"""

    def offer(state, ctx, current):
        del state
        if ctx.scope is not CandidateScope.DODGE:
            return current
        return (*current, ActivateSkill(skill_id=skill_id, answers_seq=ctx.answers_seq))

    return SkillDef(
        skill_id=skill_id,
        locked=False,
        carrier=carrier,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, offer),),
    )


def _card_of_color(type_id: str, *, black: bool) -> int:
    """某牌名里第一张黑色 / 红色的牌（判定牌用它当"没通过 / 通过"）。"""
    from sgs.data import Color, color_of_suit

    wanted = Color.BLACK if black else Color.RED
    return next(
        card_id
        for card_id in cards_of_type(PACK, type_id)
        if color_of_suit(PACK.deck.cards[card_id].suit) is wanted
    )


def _deck_before_the_first_judge(judge: int, *, used: tuple[int, ...]) -> tuple[int, ...]:
    """把 ``judge`` 放在**第 3 张**：座位 0 的摸牌阶段先取走前两张，判定取到的就是它。"""
    excluded = set(used) | {judge}
    fillers = [
        card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded
    ]
    return (fillers[0], fillers[1], judge, *fillers[2:])


class _Answers:
    """按座位的一份脚本：``handler`` 返回动作；返回 ``None`` 时退到最小合法应答。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        if query.kind == QueryKind.PLAY_PHASE:
            return next(
                (
                    action
                    for action in query.legal_actions
                    if isinstance(action, EndPhase)
                ),
                query.legal_actions[0],
            )
        return next(
            (
                action
                for action in query.legal_actions
                if isinstance(action, Pass)
            ),
            query.legal_actions[0],
        )


def _need_card_engine(*, judge: int, hand: tuple[int, ...], skills: tuple[str, ...]):
    """座位 0 用一张【杀】打座位 1；座位 1 = 需要【闪】的那一方，``skills`` 授予给它。"""
    slash = cards_of_type(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=hand)],
        deck=_deck_before_the_first_judge(judge, used=(slash, *hand, judge)),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=5)
    for skill_id in skills:
        engine.grant_skill(1, skill_id)
    return engine, slash


def _slash_resolved(engine, slash: int) -> bool:
    """那张【杀】的使用链走完并结算栈清空（与 ``test_hero_response_window`` 同判据）。"""
    return slash in engine.state.discard and not engine.stack


def _need_card_face(engine, slash: int, *, answer) -> list[tuple[ActivateSkill, ...]]:
    """把座位 1 在【闪】窗口被问到的每一张面记下来；``answer`` 逐问给出应答。"""
    faces: list[tuple] = []

    def defender(query):
        if query.kind != QueryKind.DODGE:
            return None
        faces.append(tuple(query.legal_actions))
        return answer(len(faces) - 1, query)

    run_queries(
        engine,
        [
            _Answers(lambda query: _slash_at(query, slash)),
            _Answers(defender),
        ],
        lambda e: _slash_resolved(e, slash),
    )
    return faces


def _slash_at(query, slash: int):
    """座位 0 的出牌阶段：用 ``slash`` 打座位 1（唯一的【杀】候选）。"""
    if query.kind != QueryKind.PLAY_PHASE:
        return None
    return next(
        (
            action
            for action in query.legal_actions
            if isinstance(action, UseCard)
            and action.card.card_id == slash
            and action.targets == (1,)
        ),
        None,
    )


def _providers(face) -> tuple[str, ...]:
    """一张面上的提供者 id（**次序本身**：解析序 = 档序；不排序、不去重）。"""
    return tuple(
        action.skill_id for action in face if isinstance(action, ActivateSkill)
    )


def _card_ids(face) -> tuple[int, ...]:
    """一张面上的真牌候选 id（同上，不排序）。"""
    return tuple(action.card.card_id for action in face if isinstance(action, UseCard))


def test_a_lower_tier_provider_closes_the_higher_tier_in_the_need_card_window(
    monkeypatch,
):
    """§5-1（不升档的曹操形状）：先发动**装备档**提供者 ⇒ 没通过后，武将档的提供者不再进面。

    规则原例（补充一手 §2.1.2.3）：曹操先发动【八卦阵】（装备）⇒ 判定黑 ⇒「只能使用一张
    牌名为【闪】的牌，**没有发动〖护驾〗的时机了**」。合成两条提供者（装备档 / 武将档）+
    一张真【闪】：第二次询问的面里只剩真【闪】与"放弃"。
    """
    black = _card_of_color("slash", black=True)
    dodge = cards_of_type(PACK, "dodge")[0]
    _install_fake_skills(
        monkeypatch,
        _need_card_provider(NEED_HERO, carrier=CarrierKind.HERO),
        _need_card_provider(NEED_EQUIP, carrier=CarrierKind.EQUIPMENT),
    )
    engine, slash = _need_card_engine(
        judge=black, hand=(dodge,), skills=(NEED_HERO, NEED_EQUIP)
    )

    faces = _need_card_face(
        engine,
        slash,
        answer=lambda index, query: (
            next(
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
                and action.skill_id == NEED_EQUIP
            )
            if index == 0
            else None  # 第二问：放弃（闸门不替玩家做选择）
        ),
    )

    assert len(faces) == 2, "提供者没通过 ⇒ 重开真牌窗口（一张面 + 重开，窗口结构不变）"
    assert NEED_HERO in _providers(faces[0]) and NEED_EQUIP in _providers(faces[0]), (
        "首问两档都要上脸（玩家自己决定先发动哪一档）："
        f"实得 {_providers(faces[0])}"
    )
    assert NEED_EQUIP not in _providers(faces[1]), "每提供者只问一次（既有口径）"
    assert NEED_HERO not in _providers(faces[1]), (
        "选了装备档（低档）⇒ 武将档（更高优先）不再进面（曹操例）："
        f"实得 {_providers(faces[1])}"
    )
    assert _card_ids(faces[1]) == (dodge,), (
        "真【闪】是最低档，照旧可点（规则原例：此时仍可使用一张【闪】）"
    )
    assert engine.state.player(1).hp == 3, "放弃 ⇒ 【杀】照常结算"


def test_a_failed_higher_tier_provider_leaves_the_lower_tier_open(monkeypatch):
    """§5-1 的反面对照：先发动**武将档**提供者 ⇒ 没通过后，装备档的提供者仍可点。

    "不升档"只禁"往回升"：档值越小越优先，``floor = 0``（武将档）⇒ 装备档（1）照进。
    """
    black = _card_of_color("slash", black=True)
    dodge = cards_of_type(PACK, "dodge")[0]
    _install_fake_skills(
        monkeypatch,
        _need_card_provider(NEED_HERO, carrier=CarrierKind.HERO),
        _need_card_provider(NEED_EQUIP, carrier=CarrierKind.EQUIPMENT),
    )
    engine, slash = _need_card_engine(
        judge=black, hand=(dodge,), skills=(NEED_HERO, NEED_EQUIP)
    )

    faces = _need_card_face(
        engine,
        slash,
        answer=lambda index, query: (
            next(
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
                and action.skill_id == NEED_HERO
            )
            if index == 0
            else None
        ),
    )

    assert len(faces) == 2
    assert NEED_HERO not in _providers(faces[1]), "每提供者只问一次"
    assert NEED_EQUIP in _providers(faces[1]), (
        f"先选高档（武将）⇒ 装备档照进（档值 1 ≥ floor 0）：实得 {_providers(faces[1])}"
    )


def _four_window_state():
    """一个座位同时具备**四扇窗口**的真实提供者 / 真牌（于吉 + 八卦阵 + 闪 / 桃 / 无懈）。

    于吉身上【蛊惑】是武将档（HERO）、八卦阵是装备档（EQUIPMENT）——一张【闪】面上三种档
    同时出现，故档位标签的"形状断言"不依赖任何合成技能。
    """
    from sgs.data import load_content_pack as load

    pack = load(deck_profile="standard_108")
    ids = {
        type_id: next(
            card_id
            for card_id, card in enumerate(pack.deck.cards)
            if card.type == type_id
        )
        for type_id in ("dodge", "peach", "nullify", "eight_diagrams")
    }
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(
                hero="yuji_mobile",
                hand=(ids["dodge"], ids["peach"], ids["nullify"]),
                equip=(ids["eight_diagrams"],),
            ),
            SeatSpec(),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in set(ids.values())
        ),
        turn_seat=0,
    )
    return state, ids


def test_a_need_card_face_labels_every_candidate_by_its_deliverer():
    """§5-3（形状断言，不比对排序）：四扇窗口的候选都带 ``int(priority_class)``。

    真牌候选 = ``CARD_USE``（2）、武将技能 = ``HERO``（0）、装备技能 = ``EQUIPMENT``（1）。
    档**由技能定义派生**（``SkillDef.priority_class``，票 01 的同一份声明）——本票不另写判据，
    故这里断的是"标签读的是那张声明"，不是硬编码的字典。
    """
    from sgs.effects import legal_actions_for

    state, ids = _four_window_state()
    faces = {
        "DODGE": legal_actions_for(
            state,
            0,
            QueryKind.DODGE,
            answers_seq=1,
            payload=DodgeContext(),
        ),
        "PEACH": legal_actions_for(
            state,
            0,
            QueryKind.PEACH,
            answers_seq=1,
            dying=0,
            payload=PeachContext(),
        ),
        "RESPOND": legal_actions_for(
            state,
            0,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(cause=WindowCause.DUEL, required="slash"),
        ),
        "NULLIFY": legal_actions_for(
            state,
            0,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.NULLIFY,
                required="nullify",
                mode=RespondMode.USE,
            ),
        ),
    }

    tiers = {
        kind: tuple(
            (action.kind, need_card_tier(action))
            for action in face
            if not isinstance(action, Pass)
        )
        for kind, face in faces.items()
    }
    # 真牌 = CARD_USE：闪 / 桃 / 无懈在各自的窗口里都只有这一档。
    for kind in ("DODGE", "PEACH", "NULLIFY"):
        assert ("UseCard", int(PriorityClass.CARD_USE)) in tiers[kind], (
            f"{kind} 面里的真牌候选应带 CARD_USE：实得 {tiers[kind]}"
        )
    assert ("UseCard", int(PriorityClass.CARD_USE)) not in tiers["RESPOND"], (
        "决斗窗口要的是【杀】：于吉手里没有【杀】，唯一的候选是提供者"
    )
    # 武将档：【蛊惑】在四扇窗口里都是 HERO。
    for kind in ("DODGE", "PEACH", "RESPOND", "NULLIFY"):
        assert ("ActivateSkill", int(PriorityClass.HERO)) in tiers[kind], (
            f"{kind} 面里的【蛊惑】应带 HERO：实得 {tiers[kind]}"
        )
    # 装备档：只有需要【闪】的窗口有装备提供者（【八卦阵】）。
    assert ("ActivateSkill", int(PriorityClass.EQUIPMENT)) in tiers["DODGE"], (
        f"【八卦阵】应带 EQUIPMENT：实得 {tiers['DODGE']}"
    )
    for kind in ("PEACH", "RESPOND", "NULLIFY"):
        assert ("ActivateSkill", int(PriorityClass.EQUIPMENT)) not in tiers[kind], (
            f"{kind} 窗口今天没有装备档提供者：实得 {tiers[kind]}"
        )
    assert ids["dodge"] in {
        action.card.card_id for action in faces["DODGE"] if isinstance(action, UseCard)
    }, "那张真【闪】确实在面里（档的断言不是空面）"


def test_the_need_card_gate_reads_the_same_floor_in_all_four_windows():
    """§4-2：四扇窗口共用**同一处**闸门（``legal_actions_for`` 是唯一候选生成口）。

    ``floor = 1``（上一手选了装备档）⇒ 更高优先的武将档不再进面；真牌（``CARD_USE`` = 2）
    与装备档照进。求桃 / 打出 / 无懈三扇今天只有武将档提供者（【蛊惑】），故端到端不可观测
    ——这条把四扇窗口的**接线**钉在候选生成口上（读到的是同一个 ``floor`` 字段）。
    """
    from sgs.effects import legal_actions_for

    state, _ids = _four_window_state()

    def providers(face) -> tuple[str, ...]:
        return tuple(a.skill_id for a in face if isinstance(a, ActivateSkill))

    def without_floor(kind: str, **kwargs) -> tuple:
        return legal_actions_for(state, 0, kind, answers_seq=1, **kwargs)

    dodge = without_floor(QueryKind.DODGE, payload=DodgeContext(floor=1))
    assert "hero.yuji_mobile.guohuo" not in providers(dodge), (
        "武将档（0）被 floor=1 挡掉"
    )
    assert "equip.eight_diagrams" in providers(dodge), "装备档（1）照进（1 ≥ 1）"
    assert any(isinstance(a, UseCard) for a in dodge), "真牌（2）照进"

    for kind, payload in (
        (
            QueryKind.PEACH,
            PeachContext(floor=1),
        ),
        (
            QueryKind.RESPOND,
            RespondContext(cause=WindowCause.DUEL, required="slash", floor=1),
        ),
        (
            QueryKind.RESPOND,
            RespondContext(
                cause=WindowCause.NULLIFY,
                required="nullify",
                mode=RespondMode.USE,
                floor=1,
            ),
        ),
    ):
        face = legal_actions_for(
            state, 0, kind, answers_seq=1, dying=0 if kind == QueryKind.PEACH else None,
            payload=payload,
        )
        assert "hero.yuji_mobile.guohuo" not in providers(face), (
            f"{kind} 窗口的武将档提供者同样被 floor=1 挡掉"
        )
