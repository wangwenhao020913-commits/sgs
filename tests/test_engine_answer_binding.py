"""票 02：**应答归属** —— 被接受的应答只能交给接受它的那一问。

侦察（`.scratch/nullify-provider-gap/issues/01-recon-which-hook-offers-fankui.md`）：
固定种子批里，`SKILL_ACTIVATION`「是否发动界司马懿【反馈】」（actor=1、
`Timing.DAMAGE_AFTER_TAKE`）的合法应答 `ActivateSkill('hero.jie_simayi.fankui')` 被
`Engine.ask` 无条件交还给了**下一问**（无懈收集式窗口，actor=4）；那个窗口的候选里根本
没有这条动作，于是 `provider_registered` 的 fail-closed 分支抛 `NotImplementedError`，
整局作废。根因不是候选生成，而是**应答 ↔ 询问没有绑定**。

本文件钉住引擎级的四条不变量（都由 `Engine.ask` / `Engine.submit` 承载）：

1. **不跨问**：应答只由它自己那一问取走。换一问（不同 kind / actor / 发问帧）⇒ 引擎
   照常建询问并挂起，绝不把上一条应答当成本问的回答；
2. **不重问**：被孤立的应答**留着**，等它那一问重入时取走 —— `CODING_STANDARDS.md` §3
   「已经问过、并记在帧里的决定，续跑时不许重新问」；
3. **带帧的 `ask_query` 窗口也要有身份**（票 02 复审）：`steps._dodge_window` 的【闪】与
   求桃走 `ask_query(frame=…)`，同一 actor 在不同帧的两问不得互换应答；
4. **续跑帧仍是同一问**：`MOVE_AFTER` 的续跑帧（`Engine._resume_frame`）从承载帧复制帧
   私有数据、`frame_id` 却是 0，故它必须仍认得出"这一问还是同一问"——旧实现在那里靠
   "有应答就交还"恰好也能取到，本修法必须显式保住它。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.resolution import Frame
from sgs.skills import MoveEvent
from sgs.state import (
    ActivateSkill,
    DodgeContext,
    FrameKind,
    MoveReason,
    Pass,
    QueryKind,
    RespondMode,
    SkillActivationContext,
    WindowCause,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, deck_excluding

PACK = load_content_pack(deck_profile="standard_108")

FANKUI = "hero.jie_simayi.fankui"
"""界司马懿【反馈】：本票的承载技能（`DamageAfterSkill`，**不是**牌的提供者）。"""


def _engine() -> Engine:
    """两人空场：这几条询问都不需要真牌 / 真技能，只需要"问得出来"。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=0,
    )
    return Engine.from_state(state, seed=1)


def _ask_skill_activation(engine: Engine, frame: Frame):
    """本票的那一问：actor=1 的"是否发动【反馈】"（生产形状带承载帧）。"""
    return engine.ask_query(
        QueryKind.SKILL_ACTIVATION,
        1,
        Timing.DAMAGE_AFTER_TAKE,
        frame=frame,
        payload=SkillActivationContext(skill_id=FANKUI),
    )


def _ask_dodge_window(engine: Engine, frame: Frame):
    """`steps._dodge_window` 的那一问（`ask_query(frame=…)` 的生产形状）。"""
    return engine.ask_query(
        QueryKind.DODGE,
        0,
        Timing.RESOLVE_BEFORE_EFFECT,
        frame=frame,
        payload=DodgeContext(),
    )


def _ask_nullify_window(engine: Engine, frame: Frame):
    """无懈收集式窗口的那一问（`steps._collected_response_window` 的载荷形状）。"""
    return engine.ask_respond(
        frame,
        0,
        Timing.RESOLVE_BEFORE_EFFECT,
        cause=WindowCause.NULLIFY,
        required="nullify",
        target=0,
        window_frame=frame.frame_id,
        mode=RespondMode.USE,
    )


def _queries(engine: Engine, kind: QueryKind) -> list:
    """记录流里这一类的询问（重问会多一条）。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def test_an_accepted_answer_is_not_returned_to_a_different_question():
    """【反馈】那一问的应答不得被无懈窗口吃掉（kind 与 actor 都不同）。"""
    engine = _engine()
    feedback_frame = Frame(kind=FrameKind.DAMAGE, data={"source": 0}, frame_id=160)
    assert engine.suspended(_ask_skill_activation(engine, feedback_frame)), "第一问应当挂起"
    activation = ActivateSkill(skill_id=FANKUI, answers_seq=engine.pending.seq)
    engine.submit(activation)

    nullify_frame = Frame(kind=FrameKind.RESOLVE, data={"source": 0}, frame_id=202)
    returned = _ask_nullify_window(engine, nullify_frame)

    assert engine.suspended(returned), (
        "无懈窗口拿到了【反馈】那一问的应答（应答没有被绑到接受它的那一问）："
        f"{returned!r}"
    )
    assert engine.pending is not None
    assert engine.pending.kind == QueryKind.RESPOND
    assert engine.pending.actor == 0
    assert all(isinstance(action, Pass) for action in engine.pending.legal_actions), (
        "合成局面里无懈窗口只该有 Pass——这条同时反证：被吞掉的那条应答**不在**本问候选里"
    )

    # 两问各自取走自己的应答（§3：同一问只问一次，重入取回决定）。
    nullify_answer = Pass(answers_seq=engine.pending.seq)
    engine.submit(nullify_answer)
    assert _ask_nullify_window(engine, nullify_frame) == nullify_answer, (
        "无懈窗口重入时取回的是**它自己**那条应答"
    )
    assert _ask_skill_activation(engine, feedback_frame) == activation, (
        "【反馈】那一问重入时取回它被接受的那条应答，而不是重问"
    )
    assert len(_queries(engine, QueryKind.SKILL_ACTIVATION)) == 1, "【反馈】只问一次"
    assert len(_queries(engine, QueryKind.RESPOND)) == 1, "无懈窗口只问一次"


def test_an_answer_does_not_cross_to_the_same_question_in_another_frame():
    """同一 actor、同一条无懈窗口：**另一帧**的那一问不得取走本帧的应答。"""
    engine = _engine()
    frame_a = Frame(kind=FrameKind.RESOLVE, data={"source": 0}, frame_id=101)
    frame_b = Frame(kind=FrameKind.RESOLVE, data={"source": 0}, frame_id=202)

    assert engine.suspended(_ask_nullify_window(engine, frame_a))
    answer_a = Pass(answers_seq=engine.pending.seq)
    engine.submit(answer_a)

    returned = _ask_nullify_window(engine, frame_b)
    assert engine.suspended(returned), (
        "B 帧的无懈窗口拿到了 A 帧那一问的应答（应答跨帧错配）："
        f"{returned!r}"
    )
    assert engine.pending is not None
    answer_b = Pass(answers_seq=engine.pending.seq)
    engine.submit(answer_b)

    assert _ask_nullify_window(engine, frame_a) == answer_a, "A 帧取回自己那条"
    assert _ask_nullify_window(engine, frame_b) == answer_b, "B 帧取回自己那条"
    assert len(_queries(engine, QueryKind.RESPOND)) == 2, "两帧各问一次，都不重问"


def test_two_dodge_windows_in_different_frames_do_not_share_an_answer():
    """`ask_query` 的【闪】窗口带承载帧：同一 actor 的两帧不得互换应答（复审 finding 1）。"""
    engine = _engine()
    frame_a = Frame(kind=FrameKind.RESOLVE, data={"source": 0}, frame_id=303)
    frame_b = Frame(kind=FrameKind.RESOLVE, data={"source": 0}, frame_id=404)

    assert engine.suspended(_ask_dodge_window(engine, frame_a))
    answer_a = Pass(answers_seq=engine.pending.seq)
    engine.submit(answer_a)

    returned = _ask_dodge_window(engine, frame_b)
    assert engine.suspended(returned), (
        "B 帧的【闪】窗口拿到了 A 帧那一问的应答（`ask_query` 没有承载帧身份）："
        f"{returned!r}"
    )
    assert engine.pending is not None
    answer_b = Pass(answers_seq=engine.pending.seq)
    engine.submit(answer_b)

    assert _ask_dodge_window(engine, frame_a) == answer_a, "A 帧取回自己那条"
    assert _ask_dodge_window(engine, frame_b) == answer_b, "B 帧取回自己那条"
    assert len(_queries(engine, QueryKind.DODGE)) == 2, "两帧各问一次，都不重问"


def test_the_move_after_continuation_frame_still_takes_its_own_answer():
    """`MOVE_AFTER` 的续跑帧是**同一问**：仍取回自己的应答，不重问（§3）。"""
    engine = _engine()
    carrying = Frame(kind=FrameKind.RESOLVE, data={"source": 0}, frame_id=41)
    assert engine.suspended(_ask_nullify_window(engine, carrying))
    accepted = Pass(answers_seq=engine.pending.seq)
    engine.submit(accepted)

    event = MoveEvent(
        cards=(engine.state.card(0),),
        from_zone=ZoneRef(Zone.HAND, 0),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=0,
        after_holder=None,
        actor=0,
    )
    continuation = Engine._resume_frame(carrying, event)
    assert continuation.frame_id == 0, "续跑帧的帧号恒为 0（冻结基准的坐标，不能改）"

    assert _ask_nullify_window(engine, continuation) == accepted, (
        "续跑帧是同一问：它必须取回自己的应答，而不是把玩家再问一遍"
    )
    assert len(_queries(engine, QueryKind.RESPOND)) == 1, "续跑不得重问"
