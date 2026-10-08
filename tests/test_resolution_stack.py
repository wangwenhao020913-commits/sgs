"""结算栈：显式栈帧 + 程序计数器 + 命名式恢复点（票 04）。

落点 = ``sgs.resolution`` 的公开面（帧、步骤表、控制值）+ 引擎的压栈 / 清栈记录。
骨架期没有结算帧在飞，所以这里用一张最小的步骤表把机制本身钉住。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.resolution import Frame, Goto, ResolutionStack, Step
from sgs.state import FrameKind
from sgs.timing import Timing

from support import SeatSpec, build_state

PACK = load_content_pack()


class _Host:
    def __init__(self) -> None:
        self.ran: list[str] = []

    def emit_timing(self, timing, **kwargs) -> None: ...

    def push_frame(self, kind, data, *, timing) -> None: ...

    def ask(self, build): ...




def test_steps_advance_then_pop_when_the_table_runs_out():
    stack = ResolutionStack()
    host = _Host()
    table = {
        FrameKind.RECOVER: (
            Step(lambda h, f: h.ran.append("a"), Timing.RECOVER_VALUE),
            Step(lambda h, f: h.ran.append("b"), Timing.RECOVER_AFTER),
        )
    }
    stack.push(Frame(kind=FrameKind.RECOVER))

    stack.step(table, host)
    assert host.ran == ["a"]
    assert stack.depth == 1

    stack.step(table, host)
    assert host.ran == ["a", "b"]
    assert stack.top.pc == 2

    stack.step(table, host)  # 表用完 ⇒ 出栈
    assert not stack


def test_stay_reenters_the_same_step_without_advancing_the_pc():
    stack = ResolutionStack()
    host = _Host()
    table = {FrameKind.RECOVER: (Step(lambda h, f: "stay", Timing.RECOVER_VALUE),)}
    stack.push(Frame(kind=FrameKind.RECOVER))

    stack.step(table, host)
    stack.step(table, host)

    assert stack.top.pc == 0
    assert stack.depth == 1


def test_goto_jumps_to_the_given_pc():
    stack = ResolutionStack()
    host = _Host()
    table = {
        FrameKind.RECOVER: (
            Step(lambda h, f: Goto(2), Timing.RECOVER_VALUE),
            Step(lambda h, f: h.ran.append("跳过")),
            Step(lambda h, f: h.ran.append("落到")),
        )
    }
    stack.push(Frame(kind=FrameKind.RECOVER))

    stack.step(table, host)
    assert stack.top.pc == 2

    stack.step(table, host)
    assert host.ran == ["落到"]


def test_clear_returns_the_discarded_frames_innermost_first():
    stack = ResolutionStack()
    stack.push(Frame(kind=FrameKind.USE, frame_id=1))
    stack.push(Frame(kind=FrameKind.RESOLVE, frame_id=2))
    stack.push(Frame(kind=FrameKind.DAMAGE, frame_id=3))

    discarded = stack.clear()

    assert [frame.frame_id for frame in discarded] == [3, 2, 1]
    assert not stack


def test_engine_names_the_return_point_and_discards_frames_innermost_first():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(40, 41)), SeatSpec(hp=4, hand=(46, 47))],
        deck=(0, 1),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    parent = engine.push_frame(FrameKind.USE, {"card": None}, timing=Timing.USE_CHOOSE_TARGET)
    child = engine.push_frame(FrameKind.RESOLVE, {}, timing=Timing.RESOLVE_START)

    assert child.return_to == ("USE", "RESOLVE_START"), "恢复点 = 父帧 kind + 压栈步骤的时机 id"
    assert child.frame_id == parent.frame_id + 1, "frame_id 由引擎单调分配、真实帧从 1 起"

    pushes = [record for record in engine.records if record.kind == "frame" and record.op == "push"]
    assert [record.frame_id for record in pushes] == [parent.frame_id, child.frame_id]

    engine.check_game_end(draw_reason="both_decks_empty")
    engine.finish()

    discards = [
        record for record in engine.records if record.kind == "frame" and record.op == "discard"
    ]
    assert [record.frame_id for record in discards] == [child.frame_id, parent.frame_id]
    assert engine.records[-1].kind == "game_end"
    assert engine.records[-1].check_seq == discards[-1].seq
