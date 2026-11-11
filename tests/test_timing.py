"""时机枚举的完整性：权威表恰 84 个节点（M2 定格 83 + tao-caocao 票 03 的
``RANK_COMPARE_PLACED``）、层号派生、M2 三分区、伪时机禁令。

枚举本身是票 01 冻结数据的落地；这里只断言"表没被写错"（数量、分区、伪时机禁令、
标识符只存枚举标识），"哪些节点真的被 dispatch"由 ``tests/test_timing_coverage.py``
的覆盖清单断言。

M2 定格（票 17 §3 / 票 32）把分区改为里程碑中性名：

- ``DISPATCH_WHITELIST``（53）：真的会 dispatch、可在记录流里观测到的节点；
- ``EMPTY_LOG``（11）：已实现类里**空日志**的 L2 边界与阶段间节点——枚举位保留，
  但流程结构性地不经过、因此记录流里一个都不出现；
- ``ENUM_ONLY``（19）：枚举位保留、流程走不到。

此后逐批解封：M3 的 ``MOVE_AFTER``（票 07）、wuming 票 04 的
``MAXLIFE_INCREASE_BEFORE`` / ``MAXLIFE_INCREASE_AFTER``（只解封"加体力上限"一侧；
减侧继续留枚举位）、拼点三时机，以及 chenqun-guyong 票 03 的
``STATE_FLIPPED`` / ``STATE_CHAINED``。今天实测 = 白名单 **62** / 空日志 **11** / 枚举位 **12**。

末尾几节分别是 **``MAXLIFE`` 事件帧的行为**（wuming 票 04：合成技能调
``Engine.raise_hp_max`` 的三条用例）、**L17 状态位写入口的派发**（chenqun-guyong 票 03：
翻面 / 横置的惰性事件、四轴计数的增量、幂等与「回合开始前翻回正面」两条读法）与
**时机计数纪律**（retro 票 06 / code-review C7）：规则级账本
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
from sgs.skills import PASS_STATE_KEY, SkillDef, state_change
from sgs.state import (
    ActivateSkill,
    ChainedChangeDelta,
    DodgeContext,
    EndPhase,
    FlippedChangeDelta,
    FrameKind,
    Pass,
    PeachContext,
    Phase,
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

from support import (
    RecordingPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    run_queries,
)

PACK = load_content_pack(deck_profile="basic_53")
"""合成技能 / 构造局面用最小牌堆档（与其它构造局面单测同一档）。"""


def test_enum_has_exactly_85_nodes():
    assert len(Timing) == 85


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

    assert len(implemented) == 73, (
        "M2 已实现类 64 + M3 解封的 1 个（票 07）+ wuming 票 04 解封的 2 个"
        " + huo-taishici-xunyu 票 03 解封的拼点二时机"
        " + tao-caocao 票 03 解封的 `RANK_COMPARE_PLACED`"
        " + feng-yujin 票 02 新增的 `ROUND_START`"
        " + chenqun-guyong 票 03 解封的 `STATE_FLIPPED` / `STATE_CHAINED`"
    )
    assert len(enum_only) == 12, (
        "枚举位 19 − M3 的 `MOVE_AFTER` − wuming 票 04 的加体力上限二时机"
        " − 票 03 的拼点二时机 − tao-caocao 的拼点三时机 + 该票新增的 1 个"
        "（feng-yujin 票 02 的 `ROUND_START` 是新成员、直接进已实现类）"
        " − chenqun-guyong 票 03 的状态位二时机"
    )
    assert implemented | enum_only == set(Timing)
    assert implemented & enum_only == set()


def test_the_dispatch_whitelist_and_empty_log_nodes_partition_the_implemented_set():
    dispatch = set(DISPATCH_WHITELIST)
    empty_log = set(EMPTY_LOG)

    assert len(dispatch) == 62, (
        "M2 dispatch 白名单 53 + M3 的 `MOVE_AFTER`（票 07）+ wuming 票 04 的二时机"
        " + 票 03 的拼点二时机 + tao-caocao 票 03 的 `RANK_COMPARE_PLACED`"
        " + feng-yujin 票 02 的 `ROUND_START`"
        " + chenqun-guyong 票 03 的 `STATE_FLIPPED` / `STATE_CHAINED`"
    )
    assert len(empty_log) == 11, "空日志 L2 节点恰 11 个"
    assert dispatch.isdisjoint(empty_log)
    assert dispatch | empty_log == set(IMPLEMENTED), "62 + 11 复原 73 个已实现类"
    assert dispatch | empty_log | set(ENUM_ONLY) == set(Timing)


def test_the_state_write_entries_unseal_exactly_the_two_l17_nodes():
    """chenqun-guyong 票 03（W2）：解封的恰是「翻面 / 横置」两个时机，**同层其余三个不动**。

    L17 的五个成员里只有这两个有写入口（``Engine.set_flipped`` / ``Engine.set_chained``）；
    ``HERO_REVEALED`` / ``HERO_REMOVED`` / ``CARD_SHOWN`` 继续留枚举位——它们今天没有任何
    写入口，解封它们等于把"没有派发点"伪装成"已实现"。
    """
    for timing in (Timing.STATE_FLIPPED, Timing.STATE_CHAINED):
        assert timing in IMPLEMENTED, "解封 = 写入口真的会派发它（惰性 + 无载体时零记录）"
        assert timing in DISPATCH_WHITELIST
        assert timing not in ENUM_ONLY
    for timing in (
        Timing.HERO_REVEALED,
        Timing.HERO_REMOVED,
        Timing.CARD_SHOWN,
    ):
        assert timing in ENUM_ONLY, "L17 的其余三个成员没有写入口 ⇒ 继续留枚举位"
        assert timing not in IMPLEMENTED
        assert timing not in DISPATCH_WHITELIST


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


def test_a_max_hp_drop_to_zero_kills_without_going_through_near_death():
    """票 04 的 0 档：上限减到 0 ⇒ 体力值**调整为 0**、**不进濒死**、直接死亡（规则 L431–433）。

    两个取值的组合都在这里钉住：

    - **满体力**（hp 2 / hp_max 2）减 2 ⇒ 上限 0、体力 0；
    - **非满体力**（hp 1 / hp_max 2）减 2 ⇒ 上限 0、体力**照样置 0**（规则不看减之前是否满；
      这是 0 档与"满体力同步下调"那条（L436–437）的分界）。

    记录流 = ``hp_max_change`` → ``hp_change``（按序）+ DEATH 帧；**没有** NEAR_DEATH 帧、
    也没有任何一个 ``NEAR_DEATH_*`` 时机被经过。**不兜底**（用户 2026-11-07）：
    `reduce_hp_max` 不再因"减后为 0"抛错。
    """
    for hp in (2, 1):
        state = build_state(
            pack=PACK,
            seats=[SeatSpec(hp=hp, hp_max=2), SeatSpec()],
            deck=tuple(range(10)),
        )
        engine = Engine.from_state(state, seed=7)
        carrier = Frame(kind=FrameKind.SKILL, frame_id=0)

        engine.reduce_hp_max(carrier, 0, 2)

        player = engine.state.player(0)
        assert (player.alive, player.hp, player.hp_max) == (False, 0, 0), (
            f"hp {hp} / hp_max 2：上限归零 ⇒ 体力置 0 + 直接死亡"
        )
        assert [
            (record.delta_type, record.before, record.after, record.amount)
            for record in _records_of(engine, kind="delta")
        ] == [
            ("hp_max_change", 2, 0, 2),
            ("hp_change", hp, 0, -hp),
        ], f"hp {hp}：上限先落、体力随后置 0"
        assert not [
            record
            for record in _records_of(engine, kind="frame")
            if record.frame_kind is FrameKind.NEAR_DEATH
        ], "0 档不进濒死"
        assert [
            record.frame_kind
            for record in _records_of(engine, kind="frame")
        ].count(FrameKind.DEATH) == 1, "直接死亡：DEATH 帧压了一次"
        assert not [
            record
            for record in _records_of(engine, kind="timing")
            if record.timing.value.startswith("NEAR_DEATH")
        ], "没有一个濒死时机节点被经过"
        assert not [
            record
            for record in _records_of(engine, kind="timing")
            if record.timing.value.startswith("MAXLIFE_")
        ], "减侧时机仍不解封（0 档也不例外）"


# ---------------------------------------------------------------------------
# L17 状态位写入口（W2 / 票 03）：``STATE_FLIPPED`` / ``STATE_CHAINED`` 解封
#
# 用户 2026-11-09 签 Q-W2 = **读法一**（`.scratch/chenqun-guyong/issues/03-faen-timing-gap.md`
# ②）：``Player.flipped`` / ``Player.chained`` 的**值真的变了** = 一次事件；幂等写（同值、
# 不落 delta）**不算**事件；「回合开始前翻回正面」（``frame=None`` 的规则固定流程）**算**；
# 一趟【铁索连环】横置 2 名角色 = **2 个事件**（一个座位一次）。
#
# 派发照 ``MOVE_AFTER`` 的先例**惰性**：当前局面没有注册在该时机上的实例 ⇒ 一条记录都不落
# （47 条标准金录像逐字节不变，判据 = `tools/check_goldens.sh`）。写入口是别人也在用的共享
# 原语（9 个调用点，见 `.scratch/chenqun-guyong/issues/03-faen-timing-gap.md` ⑤A），故事件
# **不在** ``set_flipped`` / ``set_chained`` 的调用栈里派发：写入口排队、下一条 ``_step``
# 的开头用一条不压栈的临时帧派发（与 ``_dispatch_phase_body`` 同形），钩子提问 / 续跑因此
# 不需要任何一个调用点透传 ``"stay"``。这些用例全部用 ``CATALOG`` 注入的合成技能钉形状。
# ---------------------------------------------------------------------------

STATE_SKILL = "hero.fake.state_flag"
"""合成 L17 技能的 id（只活在 ``CATALOG`` 注入里）。"""

STATE_TIMINGS = (Timing.STATE_FLIPPED, Timing.STATE_CHAINED)
"""本票解封的两个时机（``DISPATCH_WHITELIST`` 的增量；同层其余三个仍在枚举位）。"""


def _install_state_skill(
    monkeypatch, calls, *, locked: bool = True, draws: int = 0, condition=None
):
    """把一条**同时挂两个状态位时机**的合成技能注入 ``CATALOG``。

    ``calls`` 收集 ``(时机, 载荷)``——载荷 = :func:`~sgs.skills.state_change` 读到的写入口
    delta（``flipped_change`` / ``chained_change``）。``locked=True`` ⇒ 不问发动、回调直接跑；
    ``locked=False`` ⇒ 先问"是否发动"（【法恩】的形状：询问让派发挂起、由下一条 ``_step``
    续跑）；``draws > 0`` ⇒ 回调里"令其摸一张牌"（摸牌会内联派发 ``MOVE_AFTER``）；
    ``condition`` 原样挂到两条钩子上（票 10：模拟【法恩】的**方向**判据，缺省 = 不设条件）。
    """
    from types import MappingProxyType

    from sgs.skills import Hook
    from sgs.skills import catalog as catalog_module
    from sgs.skills.flow import SkillFlow, zero_step_effect

    def _handler(timing):
        def handler(host, frame):
            change = state_change(frame)
            calls.append((timing, change))
            if draws:
                control = host.draw_for_hand(frame, change.seat, draws)
                if control == "stay":
                    return "stay"
            return None

        return handler

    def _hook(timing, name):
        handler = _handler(timing)
        if locked:
            return Hook(timing, handler=handler, condition=condition)
        flow = SkillFlow(name=name, steps=(), effect=zero_step_effect(handler))
        return Hook(timing, flow=flow, condition=condition)

    definition = SkillDef(
        skill_id=STATE_SKILL,
        locked=locked,
        carrier=CarrierKind.HERO,
        hooks=(
            _hook(Timing.STATE_FLIPPED, "fake_state_flip"),
            _hook(Timing.STATE_CHAINED, "fake_state_chain"),
        ),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, STATE_SKILL: definition}),
    )


def _state_engine(seed: int = 1):
    """状态位事件的测试底座：两座位、空手牌、10 张牌堆、座位 0 先手。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=tuple(range(10)),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    return Engine.from_state(state, seed=seed)


def _state_records(engine) -> list:
    """记录流里的两个状态位时机记录（按序）。"""
    return [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing in STATE_TIMINGS
    ]


def _delta_records(engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def test_a_changed_flip_dispatches_state_flipped_with_the_delta_as_payload(monkeypatch):
    """**值真的变了** ⇒ 一条 ``STATE_FLIPPED`` 记录 + 一次钩子，载荷 = 那条 delta。

    读法一的第一条：事件的判据是"值变了"，故记录的主体 = 被翻面的座位、目标的同一座位、
    没有牌；钩子拿到的载荷就是写入口落的 ``flipped_change``（``seat`` + **新值**），不需要
    再读一份会漂移的当前状态。
    """
    calls: list = []
    _install_state_skill(monkeypatch, calls)
    engine = _state_engine()
    engine.grant_skill(0, STATE_SKILL)
    players = [RecordingPlayer(), RecordingPlayer()]

    assert engine.set_flipped(None, 1, True) is True
    run_queries(
        engine,
        players,
        lambda e: len(_state_records(e)) >= 1,
        limit=50,
    )

    records = _state_records(engine)
    assert [record.timing for record in records] == [Timing.STATE_FLIPPED]
    record = records[0]
    assert (record.actor, record.targets, record.cards) == (1, (1,), ())
    assert calls == [
        (Timing.STATE_FLIPPED, FlippedChangeDelta(seat=1, flipped=True))
    ], "钩子拿到的载荷 = 写入口那条 delta（座位 + 新值）"
    delta = _delta_records(engine, "flipped_change")[0]
    assert delta.seq < record.seq, "「后」= 那次写入完成之后：delta 在前、时机记录在后"


def test_one_iron_chain_chaining_two_seats_is_two_events(monkeypatch):
    """一趟【铁索连环】横置 2 名角色 ⇒ **2 个事件**（读法一：一个座位一次），按座位序。

    这也是"事件粒度 = 值变化"的直接后果：两个座位各变了一次 ⇒ 两次发动机会，不是一次。
    """
    calls: list = []
    _install_state_skill(monkeypatch, calls)
    engine = _state_engine()
    engine.grant_skill(0, STATE_SKILL)
    players = [RecordingPlayer(), RecordingPlayer()]

    assert engine.set_chained(None, 0, True) is True
    assert engine.set_chained(None, 1, True) is True
    run_queries(
        engine,
        players,
        lambda e: len(_state_records(e)) >= 2,
        limit=50,
    )

    records = _state_records(engine)
    assert [record.timing for record in records] == [
        Timing.STATE_CHAINED,
        Timing.STATE_CHAINED,
    ]
    assert [record.actor for record in records] == [0, 1], "按座位序各一次"
    assert [record.targets for record in records] == [(0,), (1,)]
    assert calls == [
        (Timing.STATE_CHAINED, ChainedChangeDelta(seat=0, chained=True)),
        (Timing.STATE_CHAINED, ChainedChangeDelta(seat=1, chained=True)),
    ]


def test_a_framed_write_lands_its_delta_on_that_frame_and_carries_the_source(monkeypatch):
    """有承载帧时（9 个调用点里的 7 个）：delta 落在该帧，事件记录继承帧的 ``source``。

    事件本身**不在**这个帧里派发（排队到下一条 ``_step``），但"这次是谁引起的"必须与写入口
    同源：``source`` 直接取承载帧的 ``source``，``frame=None``（回合环的翻回正面）留空。
    """
    calls: list = []
    _install_state_skill(monkeypatch, calls)
    engine = _state_engine()
    engine.grant_skill(0, STATE_SKILL)
    carrier = Frame(kind=FrameKind.SKILL, data={"source": 1}, frame_id=7)

    assert engine.set_chained(carrier, 0, True) is True
    run_queries(
        engine,
        [RecordingPlayer(), RecordingPlayer()],
        lambda e: len(_state_records(e)) >= 1,
        limit=50,
    )

    record = _state_records(engine)[0]
    assert (record.actor, record.source, record.targets) == (0, 1, (0,))
    assert _delta_records(engine, "chained_change")[0].frame_id == 7, "写入口的帧坐标照旧"
    assert calls == [(Timing.STATE_CHAINED, ChainedChangeDelta(seat=0, chained=True))]


def test_an_idempotent_write_is_not_an_event(monkeypatch):
    """读法一的反面：**同值重写不算事件**（幂等写不落 delta，也不派发）。

    两个写入口都自带幂等早退；本用例把它与派发绑在一起：第二次 ``True`` / 已经正面时的
    ``False`` 都既没有 delta、也没有时机记录、也没有钩子。
    """
    calls: list = []
    _install_state_skill(monkeypatch, calls)
    engine = _state_engine()
    engine.grant_skill(0, STATE_SKILL)
    players = [RecordingPlayer(), RecordingPlayer()]

    assert engine.set_chained(None, 0, True) is True
    assert engine.set_flipped(None, 1, True) is True
    run_queries(engine, players, lambda e: len(_state_records(e)) >= 2, limit=50)
    assert [record.timing for record in _state_records(engine)] == [
        Timing.STATE_CHAINED,
        Timing.STATE_FLIPPED,
    ]

    assert engine.set_chained(None, 0, True) is False, "同值 ⇒ 幂等早退"
    assert engine.set_flipped(None, 1, True) is False
    for _ in range(3):
        if engine.pending is None:
            engine._step()

    assert [record.timing for record in _state_records(engine)] == [
        Timing.STATE_CHAINED,
        Timing.STATE_FLIPPED,
    ], "幂等写不派发第二个事件"
    assert len(calls) == 2, "钩子也只跑过那两次"
    assert len(_delta_records(engine, "chained_change")) == 1
    assert len(_delta_records(engine, "flipped_change")) == 1


def test_the_turn_start_unflip_counts_as_a_flip_event(monkeypatch):
    """读法一签下的那一处：**回合开始前翻回正面也算一次翻面事件**（Q-W2，票 02 ②）。

    造局：先把座位 0 翻到背面（**此时还没授予技能** ⇒ 懒守卫为假、那一次不落事件——顺带
    钉住"守卫看的是当下的注册实例"），再授予合成技能；座位 0 的「回合开始前」翻回正面
    必须派发 ``STATE_FLIPPED``，载荷 = ``flipped_change(seat=0, flipped=False)``。
    """
    calls: list = []
    _install_state_skill(monkeypatch, calls)
    engine = _state_engine()
    engine.set_flipped(None, 0, True)  # 无载体：只有 delta，没有事件
    assert _state_records(engine) == []
    engine.grant_skill(0, STATE_SKILL)
    players = [RecordingPlayer(), RecordingPlayer()]

    run_queries(engine, players, lambda e: len(_state_records(e)) >= 1, limit=400)

    records = _state_records(engine)
    assert [record.timing for record in records] == [Timing.STATE_FLIPPED]
    assert (records[0].actor, records[0].targets) == (0, (0,))
    assert records[0].source is None, "帧外写（frame=None）：事件没有操使者"
    assert calls == [
        (Timing.STATE_FLIPPED, FlippedChangeDelta(seat=0, flipped=False))
    ], "「回合开始前翻回正面」算一次事件（载荷 = 新值 False）"
    before_starts = [
        record.seq
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_BEFORE_START
    ]
    assert before_starts, "造局没走到座位 0 的「回合开始前」"
    assert before_starts[0] < records[0].seq, "翻面在「回合开始前」之内、翻完才是事件"


def _state_flip_scene():
    """一段**含真实状态变化**的脚本：横置 / 重置 / 翻面 / 「回合开始前翻回正面」。"""
    engine = _state_engine()
    engine.set_chained(None, 0, True)
    engine.set_chained(None, 0, False)
    engine.set_chained(None, 1, True)
    engine.set_flipped(None, 0, True)
    players = [RecordingPlayer(), RecordingPlayer()]
    run_queries(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
            for record in e.records
        ),
        limit=400,
    )
    return engine


def test_no_registered_instance_keeps_the_record_stream_byte_identical(monkeypatch):
    """**惰性**的判据：没有注册实例 ⇒ 记录流与"这条派发根本不存在"逐字节相同。

    对照臂用同一个脚本跑两次：一次 ``CATALOG`` 里**有**这条合成技能的**定义**（但没人拿到它
    ⇒ :func:`~sgs.skills.resolve_instances` 不产出实例），一次连定义都没有。两边的
    ``journal()`` 必须逐字节相等，且都**只**落状态位 delta、一条 ``STATE_*`` 时机记录都没有。
    定义存在与否无痕，正是"守卫读的是**注册实例**、不是一条会漂移的 id 名单"的判据。
    """
    control = _state_flip_scene()
    _install_state_skill(monkeypatch, [])
    baseline = _state_flip_scene()

    control_text = control.journal()
    baseline_text = baseline.journal()
    assert baseline_text == control_text, "没有注册实例时记录流逐字节不变"
    for timing in STATE_TIMINGS:
        assert timing.value not in baseline_text, "一条状态位时机记录都不许落"
    assert [
        (record.seat, record.chained)
        for record in _delta_records(baseline, "chained_change")
    ] == [(0, True), (0, False), (1, True)], "状态变化本身照常落 delta"
    assert [
        (record.seat, record.flipped)
        for record in _delta_records(baseline, "flipped_change")
    ] == [(0, True), (0, False)], "含「回合开始前翻回正面」那一次"


def test_a_state_hook_can_ask_and_continue_across_the_lazy_dispatch(monkeypatch):
    """钩子**可以提问并续跑**（【法恩】的形状）：非锁定技先问"是否发动"，答发动 ⇒ 摸一张。

    派发被排到写入口之外（下一条 ``_step`` 的帧外临时帧），故挂起 / 续跑不需要任何调用点
    改签名：询问照常是 ``SKILL_ACTIVATION``，应答到达后续跑同一个派发、把那一张牌摸完。
    """
    calls: list = []
    _install_state_skill(monkeypatch, calls, locked=False, draws=1)

    class _AcceptStateSkill(RecordingPlayer):
        """技能询问一律发动；其余照脚本玩家（能结束阶段就结束阶段）。"""

        def respond(self, query):
            if query.kind == QueryKind.SKILL_ACTIVATION:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        self.seen.append(query)
                        return action
            return super().respond(query)

    engine = _state_engine()
    engine.grant_skill(0, STATE_SKILL)
    players = [_AcceptStateSkill(), RecordingPlayer()]
    hand_before = len(engine.state.player(0).hand)

    assert engine.set_flipped(None, 0, True) is True
    run_queries(
        engine,
        players,
        lambda e: bool(calls) and e.pending is None,
        limit=50,
    )

    asked = [query for query in players[0].seen if query.kind == QueryKind.SKILL_ACTIVATION]
    assert len(asked) == 1, "挂起 → 应答 → 续跑：同一个钩子只问一次（CODING_STANDARDS §3）"
    assert len(engine.state.player(0).hand) == hand_before + 1, "答发动 ⇒ 续跑后真的摸了一张"
    assert calls == [
        (Timing.STATE_FLIPPED, FlippedChangeDelta(seat=0, flipped=True))
    ]


def _chained_only(host, frame) -> bool:
    """合成【法恩】形状的**方向条件**：只有 ``chained=True`` 那一条成立（不关于持有者）。

    与 `src/sgs/skills/state_after.py:_faen_chain_is_set` 同形但**故意不复用**：本文件钉的是
    框架（条件在派发环里怎么用），真实技能的判据由 `tests/test_hero_yijiang.py` 从外部缝钉。
    """
    del host
    change = state_change(frame)
    return not isinstance(change, ChainedChangeDelta) or change.chained


def test_a_direction_condition_silences_the_ask_but_not_the_l17_dispatch(monkeypatch):
    """票 10（P1）：方向判据是**技能条件**，L17 的派发口径（Q-W2 = 读法一）一字不改。

    造局：同一 ``_step`` 之前连着两次 ``set_chained``（先横置、再重置）⇒ 两条事件都排队。
    期望：① 记录流里两条 ``STATE_CHAINED`` 时机 + 两条 ``chained_change`` 照旧（重置**不会**
    因为方向被过滤而在框架侧消失）；② 只有横置那一条进「是否发动」——重置那一条被钩子条件
    挡在询问之前。这两条合起来才说明"方向过滤落在技能侧、没有搬进派发器"。
    """
    calls: list = []
    _install_state_skill(monkeypatch, calls, locked=False, condition=_chained_only)

    class _CountAsks(RecordingPlayer):
        """技能询问一律发动，并数清问了几次。"""

        def respond(self, query):
            if query.kind == QueryKind.SKILL_ACTIVATION:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        self.seen.append(query)
                        return action
            return super().respond(query)

    engine = _state_engine()
    engine.grant_skill(0, STATE_SKILL)
    players = [_CountAsks(), RecordingPlayer()]

    assert engine.set_chained(None, 0, True) is True
    assert engine.set_chained(None, 0, False) is True, "同一 _step 里再重置一次"
    run_queries(engine, players, lambda e: len(_state_records(e)) >= 2, limit=50)

    assert [record.timing for record in _state_records(engine)] == [
        Timing.STATE_CHAINED,
        Timing.STATE_CHAINED,
    ], "两条事件都派发（重置照旧是一条 STATE_CHAINED）"
    assert [
        record.chained for record in _delta_records(engine, "chained_change")
    ] == [True, False], "两条 delta 照旧落（读法一）"
    asked = [
        query for query in players[0].seen if query.kind == QueryKind.SKILL_ACTIVATION
    ]
    assert len(asked) == 1, "只有「未横置 → 横置」那一条进询问"
    assert calls == [
        (Timing.STATE_CHAINED, ChainedChangeDelta(seat=0, chained=True))
    ], "条件不成立的那一条连回调都不跑（更不会问）"


class _SlashOnce:
    """出牌阶段对 ``target`` 使用 ``card_id`` **一次**；其余询问能放弃就放弃。"""

    def __init__(self, card_id: int, target: int) -> None:
        self.card_id = card_id
        self.target = target
        self.used = False

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and not self.used:
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == self.card_id
                    and action.targets == (self.target,)
                ):
                    self.used = True
                    return action
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
        return query.legal_actions[0]


def test_the_elemental_damage_reset_dispatches_state_chained_from_the_damage_step(monkeypatch):
    """属性伤害命中横置者 ⇒ 伤害帧里的「结算后立即重置」（`steps.py` 的写入口）也是一次事件。

    这是 spec §8 点名的那条风险路径：``set_chained`` 在**别人也在用**的伤害步骤里被调。
    事件不在该步骤里内联派发（那会让每个调用点都要透传 ``"stay"``），而是排到下一次
    ``_step``——但它真的派发了：载荷 = ``chained_change(seat=1, chained=False)``，钩子被叫醒，
    且记录排在伤害的 ``hp_change`` 之后（「后」= 写入完成之后）。
    """
    pack = load_content_pack(deck_profile="standard_ex_160")
    fire_slash = cards_of_type(pack, "fire_slash")[0]
    calls: list = []
    _install_state_skill(monkeypatch, calls)
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(fire_slash,)), SeatSpec(chained=True)],
        deck=tuple(
            card_id for card_id in range(len(pack.deck.cards)) if card_id != fire_slash
        ),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=1)
    engine.grant_skill(0, STATE_SKILL)
    attacker = _SlashOnce(fire_slash, 1)

    run_queries(
        engine,
        [attacker, _SlashOnce(-1, -1)],
        lambda e: len(_state_records(e)) >= 1,
        limit=2_000,
    )

    assert engine.state.player(1).chained is False, "属性伤害命中横置者 ⇒ 结算后立即重置"
    records = _state_records(engine)
    assert [record.timing for record in records] == [Timing.STATE_CHAINED]
    assert (records[0].actor, records[0].targets) == (1, (1,))
    assert calls == [
        (Timing.STATE_CHAINED, ChainedChangeDelta(seat=1, chained=False))
    ], "重置也是一次横置事件（读法一：值变了）"
    assert [record.chained for record in _delta_records(engine, "chained_change")] == [False]
    hurt = _delta_records(engine, "hp_change")
    assert hurt and hurt[0].seq < records[0].seq, "事件排在伤害的体力变化之后"


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


def test_the_higher_tier_is_asked_first_and_a_pass_moves_to_the_next_tier(monkeypatch):
    """§8-3（不升档；Q1/Q13 裁定为**按档逐档问**）：「武将 + 装备」两条 ⇒ 每档一张面。

    改造前是一张**跨档**的合并面：先点低档（装备）之后高档（武将）被 ``pass_floor`` 挡掉，
    玩家读成"选了一个另一个就没了"（票面根因 1）。现在档序由引擎给：

    - 首问只有**最小档**（武将 0）那一条；
    - ``Pass`` = 本档不发动 ⇒ 进下一档（装备 1），这才是"没有发动武将技"的出口；
    - 选了武将档之后装备档**照问**（档只降不升，§2.1.2.3 本来的方向）。
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

    # ① Pass 掉武将档 ⇒ 装备档才上脸。
    engine = _activation_engine(FAKE_A, FAKE_EQUIP)
    script = _FaceScript(None, FAKE_EQUIP)

    _drive(engine, script, lambda e: ran == [FAKE_EQUIP])

    assert script.faces == [(FAKE_A,), (FAKE_EQUIP,)], (
        "每档一张面、高档先问；Pass = 本档不发动、进下一档："
        f"实得 {script.faces}"
    )
    assert ran == [FAKE_EQUIP], "被放弃的那条一次都不跑"
    assert len(_activation_queries(engine)) == 2, "两档各一问"

    # ② 反面：发动武将档之后装备档照问（不升档**不**拦更低优先的档）。
    ran.clear()
    engine = _activation_engine(FAKE_A, FAKE_EQUIP)
    script = _FaceScript(FAKE_A, FAKE_EQUIP)

    _drive(engine, script, lambda e: ran == [FAKE_A, FAKE_EQUIP])

    assert script.faces == [(FAKE_A,), (FAKE_EQUIP,)], (
        f"同档内一次选一条、选完重估 ⇒ 第二问才轮到装备档：实得 {script.faces}"
    )
    assert ran == [FAKE_A, FAKE_EQUIP], "两条都跑（档只降不升）"


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


# ---------------------------------------------------------------------------
# 复核修复（F1）：强制段**按档**跑（不是"轮次开头一次全跑"），`Pass` 只结束**选择**
#
# 票 01 §4-5 的原话是「强制段…**在本档轮到它时**直接跑」，spec §5 又要求本 effort 不改
# 档间次序（武将 > 装备 > 使用牌）。改造把它读成了"全部强制段在轮次开头一次跑完"，
# 于是真实碰撞里【枭姬】（HERO 档、可选）与【白银狮子】（EQUIPMENT 档、锁定）同时命中一次
# `MOVE_AFTER` 时，锁定回复抢在可选择面**之前**结算——与 HEAD 的逐档次序相反。
# ---------------------------------------------------------------------------

XIAOJI = "hero.sunshangxiang.xiaoji"
"""真实形状里的武将档可选项（【枭姬】；白银狮子是装备档的锁定提供者）。"""

FAKE_FORCED_HERO = "hero.fake.forced_hero"
FAKE_FORCED_EQUIP = "hero.fake.forced_equip"
FAKE_MIXED = "hero.fake.face_mixed"
"""合成形状：两条不同档的锁定技（逐档跑）、一条"可选段 + 强制段"的混合技能。"""


def _silver_lion_engine():
    """孙尚香穿着【白银狮子】、手里一张新防具：用新防具顶替会**同时失去白银狮子**。

    白银狮子只在 ``standard_ex_160`` 档里；座位 0 = 孙尚香 ⇒ 这一次 ``MOVE_AFTER`` 同时命中
    两条提供者：武将档的【枭姬】（可选）与装备档的白银狮子（锁定、``MOVE_AFTER``）。
    """
    pack = load_content_pack(deck_profile="standard_ex_160")
    silver = cards_of_type(pack, "silver_lion")[0]
    armor = cards_of_type(pack, "eight_diagrams")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hp=2, hp_max=4, equip=(silver,), hero="sunshangxiang"),
            SeatSpec(),
        ],
        deck=deck_excluding(pack, silver, armor),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    engine.state.player(0).hand[:] = [armor]
    return engine, armor


class _ReplaceSilverLion:
    """出牌阶段只用那张新防具（顶替白银狮子）；技能询问一律「放弃」。"""

    def __init__(self, armor: int) -> None:
        self.armor = armor

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION:
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == self.armor:
                    return action
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        return next(
            (action for action in query.legal_actions if isinstance(action, Pass)),
            query.legal_actions[0],
        )


def _silver_lion_order(engine) -> list[tuple]:
    """这次失去的结算次序：面 → （``RECOVER`` 帧压栈）→ 体力变化。

    记录流原样取出（`CODING_STANDARDS.md` §4：不 ``sorted`` / 不取集合）。
    """
    order: list[tuple] = []
    for record in engine.records:
        if record.kind == "query" and record.query.kind == QueryKind.SKILL_ACTIVATION:
            if XIAOJI in _face_of(record.query):
                order.append(("ask", XIAOJI))
        elif (
            record.kind == "frame"
            and record.frame_kind is FrameKind.RECOVER
            and record.op == "push"
        ):
            order.append(("recover", "push"))
        elif (
            record.kind == "delta"
            and record.delta_type == "hp_change"
            and record.seat == 0
        ):
            order.append(("hp", record.before, record.after))
    return order


def _silver_lion_hp_changed(engine) -> bool:
    return any(entry[0] == "hp" for entry in _silver_lion_order(engine))


def _recover_pushes(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.frame_kind is FrameKind.RECOVER
        and record.op == "push"
    )


def test_a_locked_equipment_segment_waits_for_the_higher_tier_face():
    """复核修复 F1（档序）：【枭姬】先上脸，白银狮子**轮到自己那一档**才结算。

    真实形状：座位 0 = 孙尚香 + 【白银狮子】，出牌阶段用新防具顶替 ⇒ 一次 ``MOVE_AFTER``
    同时命中两条提供者。修前"强制段在轮次开头一次跑完"把锁定回复（压 ``RECOVER`` 帧）
    排在面**之前**；修后强制段只跑"当前最高优先档"的那几条 ⇒ 武将档（0）的面先问，
    装备档（1）的锁定段要等面答完。判据比较**记录序本身**（§4）。
    """
    engine, armor = _silver_lion_engine()
    script = _ReplaceSilverLion(armor)
    _drive(engine, script, lambda e: bool(_activation_queries(e)))

    query = engine.pending
    assert _face_of(query) == (XIAOJI,), f"面里只有武将档那条可选项：实得 {_face_of(query)}"
    assert _silver_lion_order(engine) == [("ask", XIAOJI)], (
        "白银狮子此刻还没结算：强制段在**它的档**轮到它时才跑（问完可选项之后）；"
        f"实得 {_silver_lion_order(engine)}"
    )

    engine.submit(
        next(action for action in query.legal_actions if isinstance(action, ActivateSkill))
    )
    _drive(engine, script, lambda e: _silver_lion_hp_changed(e))

    assert _silver_lion_order(engine) == [
        ("ask", XIAOJI),
        ("recover", "push"),
        ("hp", 2, 3),
    ], f"面 → 装备档的回复：实得 {_silver_lion_order(engine)}"
    assert engine.state.player(0).hp == 3, "选了枭姬也不能把白银狮子的强制段吞掉"
    assert _recover_pushes(engine) == 1, "强制段恰好结算一次"


def test_a_pass_on_the_face_still_runs_the_pending_mandatory_segment():
    """复核修复 F1（`Pass`）：问句放弃了，白银狮子的锁定回复照跑。

    ``Pass`` 结束的是这名角色的**选择**，不是它的强制效果（票 01 §4-6 + §4-5 复核读法）。
    修前 `Pass` 直接 ``_pass_next_seat`` ⇒ 更低档的锁定段整段丢掉；这里把"放弃"与
    "回复照样落地"配成同一条记录序判据。
    """
    engine, armor = _silver_lion_engine()
    script = _ReplaceSilverLion(armor)
    _drive(engine, script, lambda e: bool(_activation_queries(e)))

    query = engine.pending
    assert _silver_lion_order(engine) == [("ask", XIAOJI)]
    engine.submit(next(action for action in query.legal_actions if isinstance(action, Pass)))
    _drive(engine, script, lambda e: _silver_lion_hp_changed(e))

    assert _silver_lion_order(engine) == [
        ("ask", XIAOJI),
        ("recover", "push"),
        ("hp", 2, 3),
    ], f"放弃只结束选择，强制段照跑：实得 {_silver_lion_order(engine)}"
    assert engine.state.player(0).hp == 3
    assert _recover_pushes(engine) == 1, "放弃之后强制段恰好跑一次"


def test_the_forced_segments_of_different_tiers_run_their_own_tier(monkeypatch):
    """复核修复 F1（逐档循环）：两条不同档的锁定技都要跑到，且按档序。

    没有可选项时"只跑当前最高优先档"意味着**一轮跑一档**：武将档先跑，回环重收集后
    装备档再跑。少了回环（或把空面直接当成"这个座位结束了"）第二条会被静默吞掉。
    """
    from sgs.skills import Hook, SkillDef

    order: list[str] = []

    def locked(skill_id, carrier, label):
        return SkillDef(
            skill_id=skill_id,
            locked=True,
            carrier=carrier,
            hooks=(
                Hook(Timing.GAME_START, lambda host, frame: order.append(label)),
            ),
        )

    _install_fake_skills(
        monkeypatch,
        locked(FAKE_FORCED_HERO, CarrierKind.HERO, "hero"),
        locked(FAKE_FORCED_EQUIP, CarrierKind.EQUIPMENT, "equip"),
    )
    engine = _activation_engine(FAKE_FORCED_HERO, FAKE_FORCED_EQUIP)
    script = _FaceScript()

    _drive(engine, script, lambda e: order == ["hero", "equip"])

    assert order == ["hero", "equip"], "武将档的强制段先于装备档（档序，不是 id 序）"
    assert script.faces == [], "两条都是强制段：一张面都不给"


def test_a_mixed_instance_runs_its_forced_item_after_the_player_passes(monkeypatch):
    """复核修复 F1（混合实例）：一条技能里既有可选段又有强制段 ⇒ 放弃后强制项照跑。

    牌面写「你可以…」的那一段要问；同一句话里没有「可以」的那几句（监听 / 清扫）不问。
    ``Pass`` 只跳过**可选段**——改造前 HEAD 的 ``if not (forced or accepted): continue``
    就是这条，票 01 的"Pass = 结束本座位"把它整条吞掉了。
    """
    from sgs.skills import Hook, SkillDef

    ran: list[str] = []
    mixed = SkillDef(
        skill_id=FAKE_MIXED,
        locked=False,
        carrier=CarrierKind.HERO,
        hooks=(
            Hook(
                Timing.GAME_START,
                handler=lambda host, frame: ran.append("forced"),
                forced=True,
            ),
            Hook(Timing.GAME_START, handler=lambda host, frame: ran.append("optional")),
        ),
    )
    _install_fake_skills(monkeypatch, mixed)
    engine = _activation_engine(FAKE_MIXED)
    script = _FaceScript(None)  # 对这条技能的"是否发动"答放弃

    _drive(
        engine,
        script,
        lambda e: bool(ran)
        or (e.pending is not None and e.pending.kind != QueryKind.SKILL_ACTIVATION),
    )

    assert script.faces == [(FAKE_MIXED,)], (
        "混合实例仍要问一次（可选段）——面上只有它自己那条候选；"
        f"实得 {script.faces}"
    )
    assert ran == ["forced"], "放弃只跳过可选段：强制段照跑，可选项不跑"
    assert len(_activation_queries(engine)) == 1


def test_the_seam_path_carries_tiers_and_the_same_gate(monkeypatch):
    """§6：``dispatch_seam`` 与 ``dispatch_timing`` 共用 ``_dispatch`` ⇒ 接缝候选同样带档、
    同样过闸门、同样**逐档问**（接缝不是另一套结算）。

    判据 = 接缝首问只有最小档（武将）那一条；``Pass`` ⇒ 第二问才是装备档；选完后收口。
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
    first = engine.pending
    assert _face_of(first) == (FAKE_A,), (
        f"接缝首问只有最小档（武将 0）那一条；实得 {_face_of(first)}"
    )
    engine.submit(Pass(answers_seq=first.seq))

    assert engine.dispatch_seam(frame, SeamKind.EQUIPMENT_DISCARD) == "stay"
    second = engine.pending
    assert _face_of(second) == (FAKE_EQUIP,), (
        f"Pass = 本档不发动 ⇒ 第二问才轮到装备档；实得 {_face_of(second)}"
    )
    engine.submit(ActivateSkill(skill_id=FAKE_EQUIP, answers_seq=second.seq))

    assert engine.dispatch_seam(frame, SeamKind.EQUIPMENT_DISCARD) is None
    assert ran == [FAKE_EQUIP], "被放弃的武将档一次都不跑；装备档照跑"
    assert len(_activation_queries(engine)) == 2


JIANYING = "hero.jie_jushou_mobile.jianying"
FAN = "equip.vermilion_feather_fan"
"""【界沮授·渐营】①（武将档 0）与【朱雀羽扇】（装备档 1）——两条真实钩子同挂 ``USE_ON_USE``。"""


def _standard_card(pack, type_id: str, *, suit: str) -> int:
    """内容包里第一张"某牌名 + 某花色"的牌（本用例要两张**同花色**的牌）。"""
    return next(
        card_id
        for card_id, card in enumerate(pack.deck.cards)
        if card.type == type_id and card.suit.value == suit
    )


def _run_jianying_fan(*, decline_jianying: bool):
    """真引擎：先用一张牌、再用一张**同花色**的普通【杀】，记下同一时机上的每一张面。

    返回 ``(faces, engine, slash)``；``faces`` 是``USE_ON_USE`` 派发的每张面的
    ``ActivateSkill`` 序列。
    """
    pack = load_content_pack(deck_profile="standard_ex_160")
    ex_nihilo = _standard_card(pack, "ex_nihilo", suit="heart")
    slash = _standard_card(pack, "slash", suit="heart")
    fan = _standard_card(pack, "vermilion_feather_fan", suit="diamond")
    dodge = _standard_card(pack, "dodge", suit="heart")
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero="jie_jushou_mobile", hand=(ex_nihilo, slash), equip=(fan,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=deck_excluding(pack, ex_nihilo, slash, fan, dodge),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=3)
    faces: list[tuple[str, ...]] = []
    played = {"first": False, "slash": False}

    def seat0(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if not played["first"]:
                played["first"] = True
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, UseCard)
                    and action.card.card_id == ex_nihilo
                )
            if not played["slash"]:
                played["slash"] = True
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (1,)
                )
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if query.kind == QueryKind.SKILL_ACTIVATION:
            faces.append(_face_of(query))
            skill_id = str(query.skill_id)
            if skill_id == JIANYING and decline_jianying:
                return next(
                    action for action in query.legal_actions if isinstance(action, Pass)
                )
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
        return None

    run_queries(
        engine,
        [_Answers(seat0), _Answers()],
        lambda e: slash in e.state.discard and not e.stack,
    )
    return faces, engine, slash


def test_jianying_is_asked_before_the_vermilion_feather_fan_at_the_same_timing():
    """Q1 / Q17（真引擎）：同一时机上的两条**不同档**的钩子 ⇒ 武将档先问、装备档后问。

    【界沮授·渐营】①（武将档 0）与【朱雀羽扇】（装备档 1）都挂在 ``USE_ON_USE`` 上：
    先使用一张牌、再使用一张**同花色**的普通【杀】⇒ 两条同时成立。改造前它们混在一张面上
    （先点羽扇 ⇒ 渐营档更优先、被 ``pass_floor`` 挡掉 = 票面报的"选了一个另一个就没了"，
    且结果与点击顺序相关）；现在每档一张面、档序由引擎给。

    **两种应答都不丢第二条**：Pass 掉渐营 ⇒ 羽扇才上脸；发动渐营 ⇒ 羽扇**照问**
    （档只降不升，§2.1.2.3 本来的方向）。
    """
    for decline in (True, False):
        faces, engine, slash = _run_jianying_fan(decline_jianying=decline)

        assert faces == [(JIANYING,), (FAN,)], (
            f"先问渐营（武将档）、再问羽扇（装备档）；decline={decline} 时实得 {faces}"
        )
        assert slash in engine.state.discard, "两张面都答完之后【杀】照常结算"


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
    """§5-1（不升档的曹操形状）：档只降不升——过了装备档之后，武将档的提供者不再进面。

    规则原例（补充一手 §2.1.2.3）：曹操先发动【八卦阵】（装备）⇒ 判定黑 ⇒「只能使用一张
    牌名为【闪】的牌，**没有发动〖护驾〗的时机了**」。合成两条提供者（装备档 / 武将档）+
    一张真【闪】。票 04 起**档序由引擎给**：Pass 掉武将档（本档不发动）⇒ 装备档那一问 ⇒
    判定黑（没通过）⇒ 牌档那一问。武将档在任何后续面里都不再出现。
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
            if index == 1
            else None  # 第 0 问 = 放弃武将档；之后的问：放弃（引擎不替玩家做选择）
        ),
    )

    assert len(faces) == 3, "三档各一张面：武将档（Pass）→ 装备档（黑）→ 牌档"
    assert _providers(faces[0]) == (NEED_HERO,), (
        f"首问只有最小档（武将 0）那一条：实得 {_providers(faces[0])}"
    )
    assert _providers(faces[1]) == (NEED_EQUIP,), (
        f"Pass = 本档不发动 ⇒ 第二问是装备档：实得 {_providers(faces[1])}"
    )
    assert NEED_HERO not in _providers(faces[1]) and NEED_HERO not in _providers(faces[2]), (
        "武将档已经过完 ⇒ 任何后续面里都不再出现（档只降不升）："
        f"实得 {_providers(faces[1])} / {_providers(faces[2])}"
    )
    assert _card_ids(faces[2]) == (dodge,), (
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

    assert len(faces) == 3, "武将档（没通过）→ 装备档 → 牌档"
    assert NEED_HERO not in _providers(faces[1]), "每提供者只问一次"
    assert NEED_EQUIP in _providers(faces[1]), (
        f"先选高档（武将）⇒ 装备档照进（档值 1 ≥ floor 0）：实得 {_providers(faces[1])}"
    )
    assert _card_ids(faces[2]) == (dodge,), "再往下一档才是真【闪】"


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
    """§5-3（形状断言，不比对排序）：每条候选的档都读**技能定义上的那一声明**。

    真牌 = ``CARD_USE``（2）；提供者 = ``SkillDef.need_card_tier``，缺省按载体派生
    （【八卦阵】= 装备档 1、【蛊惑】= 覆盖位 2）。票 04 起面是**逐档**的，故按档走进每一张面
    各看一眼：一张面里的候选**必然同档**，且档值与定义一致（不是硬编码的字典）。
    """
    from sgs.effects import legal_actions_for
    from sgs.skills import CATALOG

    state, ids = _four_window_state()

    def face(kind, payload, dying=None) -> tuple:
        return legal_actions_for(
            state, 0, kind, answers_seq=1, dying=dying, payload=payload
        )

    def declared_tier(action) -> int:
        if not isinstance(action, ActivateSkill):
            return int(PriorityClass.CARD_USE)
        definition = CATALOG[action.skill_id]
        override = definition.need_card_tier
        return int(override) if override is not None else int(definition.priority_class)

    faces = {
        "DODGE@0": face(QueryKind.DODGE, DodgeContext()),
        "DODGE@1": face(QueryKind.DODGE, DodgeContext(floor=1)),
        "DODGE@2": face(QueryKind.DODGE, DodgeContext(floor=2)),
        "PEACH": face(QueryKind.PEACH, PeachContext(), dying=0),
        "RESPOND": face(
            QueryKind.RESPOND, RespondContext(cause=WindowCause.DUEL, required="slash")
        ),
        "NULLIFY": face(
            QueryKind.RESPOND,
            RespondContext(
                cause=WindowCause.NULLIFY, required="nullify", mode=RespondMode.USE
            ),
        ),
    }
    tiers = {
        label: tuple(
            (action.kind, need_card_tier(action))
            for action in actions
            if not isinstance(action, Pass)
        )
        for label, actions in faces.items()
    }

    # 装备档（【八卦阵】）：floor 0 / 1 两张面都只到它（最小档 ≥ floor），牌档还不在面里。
    for label in ("DODGE@0", "DODGE@1"):
        assert tiers[label] == (("ActivateSkill", int(PriorityClass.EQUIPMENT)),), (
            f"{label} 面应是装备档那一条：实得 {tiers[label]}"
        )
    # 牌档（floor = 2）：真【闪】与【蛊惑】同档 ⇒ **同一张面**（Q16 的程序化约定）。
    assert ("UseCard", int(PriorityClass.CARD_USE)) in tiers["DODGE@2"], (
        f"真【闪】在牌档面上：实得 {tiers['DODGE@2']}"
    )
    assert ("ActivateSkill", int(PriorityClass.CARD_USE)) in tiers["DODGE@2"], (
        f"【蛊惑】也必须在牌档面上（覆盖位 = 2）：实得 {tiers['DODGE@2']}"
    )
    # 其余三扇窗口：今天唯一的提供者是【蛊惑】（牌档），真牌与它同面。
    for label, expected_card in (
        ("PEACH", ("UseCard", int(PriorityClass.CARD_USE))),
        ("NULLIFY", ("UseCard", int(PriorityClass.CARD_USE))),
    ):
        assert expected_card in tiers[label], (
            f"{label} 面里的真牌候选应带 CARD_USE：实得 {tiers[label]}"
        )
    assert tiers["RESPOND"] == (("ActivateSkill", int(PriorityClass.CARD_USE)),), (
        "决斗窗口要的是【杀】：于吉手里没有【杀】，面上的提供者是牌档的【蛊惑】："
        f"实得 {tiers['RESPOND']}"
    )

    for label, actions in faces.items():
        for action in actions:
            if isinstance(action, Pass):
                continue
            assert need_card_tier(action) == declared_tier(action), (
                f"{label} 面里的 {action.kind} 档值必须与定义一致："
                f"{need_card_tier(action)} != {declared_tier(action)}"
            )
        non_pass = {need_card_tier(a) for a in actions if not isinstance(a, Pass)}
        assert len(non_pass) <= 1, f"{label} 面里混了多个档：{non_pass}（票 04 必须分面）"
    assert ids["dodge"] in {
        action.card.card_id
        for action in faces["DODGE@2"]
        if isinstance(action, UseCard)
    }, "那张真【闪】确实在牌档面里（档的断言不是空面）"


def test_the_need_card_gate_reads_the_same_floor_in_all_four_windows():
    """§4-2 / 票 04：四扇窗口共用**同一处**分面口（``legal_actions_for`` 是唯一候选生成口）。

    档的输入是同一个 ``floor`` 字段，行为也必须是同一条规则：``floor = 0`` ⇒ 面 = 最小档；
    ``floor`` 落在某一档上 ⇒ 面 = **该档**；``floor`` 超过全部档 ⇒ 只剩 ``Pass``。四扇窗口
    逐一看一遍，故"只有三扇接线"会立刻红。
    """
    from sgs.effects import legal_actions_for

    state, ids = _four_window_state()

    def face(kind, payload, dying=None) -> tuple:
        return legal_actions_for(
            state, 0, kind, answers_seq=1, dying=dying, payload=payload
        )

    def providers(actions) -> tuple[str, ...]:
        return tuple(a.skill_id for a in actions if isinstance(a, ActivateSkill))

    windows = (
        ("DODGE", QueryKind.DODGE, lambda floor: DodgeContext(floor=floor), None),
        ("PEACH", QueryKind.PEACH, lambda floor: PeachContext(floor=floor), 0),
        (
            "RESPOND",
            QueryKind.RESPOND,
            lambda floor: RespondContext(
                cause=WindowCause.DUEL, required="slash", floor=floor
            ),
            None,
        ),
        (
            "NULLIFY",
            QueryKind.RESPOND,
            lambda floor: RespondContext(
                cause=WindowCause.NULLIFY,
                required="nullify",
                mode=RespondMode.USE,
                floor=floor,
            ),
            None,
        ),
    )
    for label, kind, make, dying in windows:
        first = face(kind, make(0), dying=dying)
        assert any(not isinstance(action, Pass) for action in first), (
            f"{label} 的首问不是空面"
        )
        past = face(kind, make(99), dying=dying)
        assert all(isinstance(action, Pass) for action in past), (
            f"{label}：floor 超过全部档 ⇒ 只剩 Pass（同一个 floor 字段、同一条规则）"
        )

    # 逐档不混面：DODGE 的 floor 0 / 1 / 2 依次是装备档、装备档、牌档。
    assert providers(face(QueryKind.DODGE, DodgeContext(floor=0))) == (
        "equip.eight_diagrams",
    ), "floor = 0 ⇒ 最小档（装备档）那一张面"
    assert providers(face(QueryKind.DODGE, DodgeContext(floor=1))) == (
        "equip.eight_diagrams",
    ), "floor = 1 ⇒ 仍是装备档（1 ≥ 1），牌档不在面里"
    assert not any(
        isinstance(action, UseCard)
        for action in face(QueryKind.DODGE, DodgeContext(floor=1))
    ), "牌档（2）不与装备档同面"
    at_two = face(QueryKind.DODGE, DodgeContext(floor=2))
    assert ids["dodge"] in {
        action.card.card_id for action in at_two if isinstance(action, UseCard)
    }, "floor = 2 ⇒ 牌档面里有那张真【闪】"
    assert "hero.yuji_mobile.guohuo" in providers(at_two), "牌档面里也有【蛊惑】"


# ---------------------------------------------------------------------------
# 同座位同档的多段强制效果：结算顺序归玩家（lei-shenganning 票 95）
#
# 用户 2026-11-07 口径：锁定侧与非锁定侧**不混同一张面**；全锁定面**有 Pass**（语义 = 剩下按
# 默认次序跑完）；全非锁定且都不可放弃的面**没有 Pass**（必须选一条）；只有一段时仍不问。
# ---------------------------------------------------------------------------


def _forced_pair(monkeypatch, *, locked: bool, tag: str):
    """造两条同座位同档的 ``GAME_START`` 强制段；返回 ``(id_a, id_b, order)``。"""
    from sgs.effects import CarrierKind
    from sgs.skills import Hook, SkillDef

    order: list[str] = []
    first = f"hero.fake.{tag}_a"
    second = f"hero.fake.{tag}_b"
    definitions = []
    for skill_id in (first, second):
        def handler(host, frame, skill_id=skill_id):
            del host, frame
            order.append(skill_id)
            return None

        definitions.append(
            SkillDef(
                skill_id=skill_id,
                locked=locked,
                carrier=CarrierKind.HERO,
                hooks=(Hook(Timing.GAME_START, handler=handler, forced=True),),
            )
        )
    _install_fake_skills(monkeypatch, *definitions)
    return first, second, order


def _face_has_pass(query) -> bool:
    return any(isinstance(action, Pass) for action in query.legal_actions)


def test_two_forced_segments_of_one_seat_ask_the_order(monkeypatch):
    """两条**非锁定**强制段撞同一时机 ⇒ 同一张面、**没有 Pass**、选谁先跑谁先跑。"""
    first, second, order = _forced_pair(monkeypatch, locked=False, tag="order")
    engine = _activation_engine(first, second)
    script = _FaceScript(second, first)

    _drive(engine, script, lambda e: order == [second, first])

    assert script.faces == [(first, second)], "两条强制段同上一张面（次序按链序）"
    assert not _face_has_pass(_activation_queries(engine)[0]), (
        "全非锁定且都不可放弃 ⇒ 面里没有 Pass（必须选一条）"
    )


def test_a_single_forced_segment_does_not_ask(monkeypatch):
    """只有一段强制段 ⇒ 仍不问、直接跑（与票 01 / 既有录像逐字节同形）。"""
    first, _second, order = _forced_pair(monkeypatch, locked=False, tag="single")
    engine = _activation_engine(first)
    script = _FaceScript()

    _drive(engine, script, lambda e: order == [first])

    assert script.faces == [], "一段强制段不上脸"
    assert not _activation_queries(engine), "也不落 SKILL_ACTIVATION 询问"


def test_a_locked_order_face_has_pass_and_runs_the_rest_by_default(monkeypatch):
    """锁定侧的面**有** Pass（用户口径：没有放弃选项，出口 = 剩下按默认次序跑完）。"""
    first, second, order = _forced_pair(monkeypatch, locked=True, tag="lorder")
    engine = _activation_engine(first, second)
    script = _FaceScript(None)  # 右端出口 = 剩下按默认次序跑完

    _drive(engine, script, lambda e: order == [first, second])

    assert script.faces == [(first, second)]
    assert _face_has_pass(_activation_queries(engine)[0]), "全锁定面有 Pass（= 默认次序）"


def test_a_pass_on_a_mixed_face_declines_the_optional_and_keeps_the_forced(monkeypatch):
    """非锁定侧：可放弃的段与 ``Hook.forced`` 段**同面**；Pass 只放弃可放弃的那些。"""
    from sgs.effects import CarrierKind
    from sgs.skills import Hook, SkillDef

    order: list[str] = []
    forced_id = "hero.fake.mix_forced"
    optional_id = "hero.fake.mix_optional"
    forced = SkillDef(
        skill_id=forced_id,
        locked=False,
        carrier=CarrierKind.HERO,
        hooks=(
            Hook(
                Timing.GAME_START,
                handler=lambda host, frame: order.append("forced"),
                forced=True,
            ),
        ),
    )
    optional = _fake_skill(
        optional_id, handler=lambda host, frame: order.append("optional")
    )
    _install_fake_skills(monkeypatch, forced, optional)
    engine = _activation_engine(forced_id, optional_id)
    script = _FaceScript(None)

    _drive(engine, script, lambda e: order == ["forced"])

    assert set(script.faces[0]) == {forced_id, optional_id}, "两段同面（先选哪条归玩家）"
    assert _face_has_pass(_activation_queries(engine)[0]), (
        "面里有可放弃的段 ⇒ 有 Pass，它只放弃那一段"
    )
