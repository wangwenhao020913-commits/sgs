"""结算栈的实现形态：**显式栈帧 + 程序计数器 + 每类帧一张步骤表**（票 04 §1）。

引擎是一台解释器：整局的控制流是**数据**（帧列表 + ``pc``），不是 Python 调用栈。
每类帧配一张 ``STEPS[kind]`` 步骤表，步骤函数返回三种控制值：

- ``None``   —— 做完这一步，``pc`` 前进；
- ``"stay"`` —— 原地重入同一步（用于询问暂停与循环）；
- :class:`Goto` —— 跳到指定 ``pc``（如【闪】抵消后跳过生效时 / 生效后）。

**恢复点是命名式**：权威是父帧的 ``pc``（下一个要执行的步骤），子帧的
``return_to = (父帧 kind, 压栈那一步对应的时机 id)`` 只是把它翻译成人话，写进记录流。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol, TypeAlias

from sgs.state import FrameKind
from sgs.timing import Timing

__all__ = ["Control", "Frame", "Goto", "ResolutionStack", "Step", "StepHost", "StepTable"]


class Goto(NamedTuple):
    """跳转控制值：下一次执行从 ``pc`` 开始。"""

    pc: int


Control: TypeAlias = str | Goto | None
"""步骤函数的返回值：``None`` 前进 / ``"stay"`` 原地重入 / :class:`Goto` 跳转。"""


class StepHost(Protocol):
    """步骤函数能看到的最小引擎面（鸭子类型，避免 ``resolution`` 反向依赖 ``engine``）。"""

    def emit_timing(self, timing: Timing, **kwargs: Any) -> None: ...

    def push_frame(self, kind: FrameKind, data: dict[str, Any], *, timing: Timing) -> None: ...

    def ask(self, build: Callable[[int], Any]) -> Any: ...


@dataclass
class Frame:
    """一个结算单元；``data`` 是帧私有载荷，``pc`` 是权威恢复点。"""

    kind: FrameKind
    data: dict[str, Any] = field(default_factory=dict)
    pc: int = 0
    return_to: tuple[str, str] | None = None
    frame_id: int = 0
    """引擎单调分配的帧标识（真实帧从 1 起；``0`` 是"无帧 / 引擎初始化"哨兵）。"""


@dataclass(frozen=True, slots=True)
class Step:
    """步骤表的一格：``run`` 干这件事，``timing`` 是它 dispatch 的时机。

    子帧的 ``return_to`` 用父帧当前步骤的 ``timing`` 作可读标签（票 04 §4）。
    """

    run: Callable[[StepHost, Frame], Any]
    timing: Timing | None = None


StepTable = Mapping[FrameKind, tuple[Step, ...]]


class ResolutionStack:
    """显式栈：只有压栈、出栈、取顶、清栈与"执行一步"五件事。

    ``frame_id`` 由引擎单调分配（真实帧从 1 起；0 是"无帧 / 引擎初始化"哨兵），
    本类不管分配，只管帧的顺序与 ``pc``。
    """

    def __init__(self) -> None:
        self.frames: list[Frame] = []

    def __len__(self) -> int:
        return len(self.frames)

    def __bool__(self) -> bool:
        return bool(self.frames)

    @property
    def top(self) -> Frame:
        return self.frames[-1]

    @property
    def depth(self) -> int:
        return len(self.frames)

    def push(self, frame: Frame) -> None:
        self.frames.append(frame)

    def pop(self) -> Frame:
        return self.frames.pop()

    def clear(self) -> tuple[Frame, ...]:
        """清空并返回被丢弃的帧，**内层在前**（对应逐帧 ``DISCARD_FRAME`` 的顺序）。"""
        discarded = tuple(reversed(self.frames))
        self.frames.clear()
        return discarded

    def step(self, steps: StepTable, host: StepHost) -> Frame | None:
        """执行栈顶帧的当前步骤一步；步骤表用完即出栈并返回被弹出的帧。

        返回值只用于让引擎记一条 ``pop`` 记录——**所有**出栈都必须经过调用方，
        这样"帧的生命周期"在记录流里没有缺口（票 06 §1）。
        """
        frame = self.top
        table = steps.get(frame.kind)
        if table is None:
            raise KeyError(f"没有为帧类型 {frame.kind.value!r} 登记步骤表")
        if frame.pc >= len(table):
            return self.pop()
        control = table[frame.pc].run(host, frame)
        if control == "stay":
            return None
        if isinstance(control, Goto):
            frame.pc = control.pc
            return None
        frame.pc += 1
        return None
