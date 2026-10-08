"""各帧的步骤表：把官方流程写成**数据**（票 04 §1，票 07 §2–§3）。

- **USE 帧**（使用一张牌）：L4 六个时机 → 压入 RESOLVE 子帧 → L5.6 把处理区里的
  实体牌置入弃牌堆。实体牌在 L4.1 / L4.2 之间从手牌移入处理区（票 07 §3）。
- **RESOLVE 帧**（逐目标结算）：L5 五个时机；"生效前"按**行为表**决定是否开响应窗口，
  被抵消则 ``Goto(RESOLVE_END_PC)`` 跳过"生效时 / 生效后"，直达"使用结算结束时"。

DAMAGE / RECOVER 的步骤表留给票 20 / 21：本票只保证它们能被压入并弹出——
否则一张未被抵消的【杀】会把引擎打崩。这不是"实现了伤害"。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from sgs.effects import EffectStep, PushFrame, behavior_for, opens_response_window
from sgs.resolution import Control, Frame, Goto, Step, StepHost, StepTable
from sgs.state import CardRef, FrameKind, GameState, MoveReason, QueryKind, UseCard, Zone
from sgs.timing import Timing

__all__ = ["RESOLVE_END_PC", "build_steps"]


class FrameHost(StepHost, Protocol):
    """步骤函数看到的**引擎面**：在票 04 的最小 ``StepHost`` 上补出使用流程要的操作。

    引擎（:class:`~sgs.engine.Engine`）结构上满足它；步骤表只经这些方法改状态，
    不直接摸结算栈，也不 import 引擎类型（避免 ``engine.steps`` ↔ ``engine.game`` 循环）。
    """

    state: GameState

    def emit_frame_timing(
        self, frame: Frame, timing: Timing, *, actor: int | None = None, cards: Sequence[CardRef] = ()
    ) -> None: ...

    def move_used_card_to_processing(self, frame: Frame) -> None: ...

    def move_processing_card_to_discard(self, frame: Frame) -> None: ...

    def push_frame(self, kind: FrameKind, data: dict[str, Any], *, timing: Timing) -> Frame: ...

    def ask_query(self, kind: QueryKind, actor: int, timing: Timing) -> Any: ...

    def suspended(self, value: Any) -> bool: ...

    def start_use(self, card: CardRef, *, source: int, targets: tuple[int, ...]) -> Frame: ...

    def apply_effects(
        self, frame: Frame, effects: Sequence[EffectStep], *, timing: Timing
    ) -> None: ...


def _emit(
    host: FrameHost,
    frame: Frame,
    timing: Timing,
    *,
    actor: int | None = None,
    cards: Sequence[CardRef] = (),
) -> None:
    host.emit_frame_timing(frame, timing, actor=actor, cards=cards)


def _effect_actor(frame: Frame) -> int:
    """"生效时 / 生效后"的主体：有目标时是目标，无目标时是使用者（票 04 原型口径）。"""
    targets = frame.data["targets"]
    return targets[0] if targets else frame.data["source"]


# ---- USE 帧：L4 六个时机 + 压入 RESOLVE + L5.6 ----------------------------


def use_choose_target(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.USE_CHOOSE_TARGET, actor=frame.data["source"])
    # 实体牌离开手牌、进入处理区：发生在 L4.1 与 L4.2 之间（票 07 §3）。
    host.move_used_card_to_processing(frame)


def use_on_use(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.USE_ON_USE, actor=frame.data["source"])


def use_designate_target(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.USE_DESIGNATE_TARGET, actor=frame.data["source"])


def use_become_target(host: FrameHost, frame: Frame) -> None:
    targets = frame.data["targets"]
    _emit(host, frame, Timing.USE_BECOME_TARGET, actor=targets[0] if targets else None)


def use_after_designate_target(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.USE_AFTER_DESIGNATE_TARGET, actor=frame.data["source"])


def use_after_become_target(host: FrameHost, frame: Frame) -> None:
    targets = frame.data["targets"]
    _emit(host, frame, Timing.USE_AFTER_BECOME_TARGET, actor=targets[0] if targets else None)


def use_push_resolve(host: FrameHost, frame: Frame) -> None:
    host.push_frame(FrameKind.RESOLVE, dict(frame.data), timing=Timing.RESOLVE_START)


def use_after_end(host: FrameHost, frame: Frame) -> None:
    host.move_processing_card_to_discard(frame)
    _emit(
        host,
        frame,
        Timing.RESOLVE_AFTER_END,
        actor=frame.data["source"],
        cards=(frame.data["card"],),
    )


# ---- RESOLVE 帧：L5 五个时机 ----------------------------------------------


def resolve_start(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.RESOLVE_START, actor=frame.data["source"])


def resolve_before_effect(host: FrameHost, frame: Frame) -> Control:
    if "canceled" in frame.data:
        # 响应窗口已经处理过：**按 canceled 标志**决定去向（票 07 §3）。
        return Goto(RESOLVE_END_PC) if frame.data["canceled"] else None
    behavior = behavior_for(frame.data["card"])
    targets = frame.data["targets"]
    if not targets or not opens_response_window(behavior):
        _emit(host, frame, Timing.RESOLVE_BEFORE_EFFECT, actor=frame.data["source"])
        frame.data["canceled"] = False
        return None
    if not frame.data.get("window_open"):
        _emit(host, frame, Timing.RESOLVE_BEFORE_EFFECT, actor=targets[0])
        frame.data["window_open"] = True
    target = targets[0]
    action = host.ask_query(QueryKind.DODGE, target, Timing.RESOLVE_BEFORE_EFFECT)
    if host.suspended(action):
        return "stay"
    frame.data["canceled"] = isinstance(action, UseCard)
    if frame.data["canceled"]:
        # 抵消是"被响应帧"的标志；【闪】自己作为一次**使用**照走 L4 / L5（票 07 §3）。
        host.start_use(action.card, source=target, targets=())
    # 重入本步：等【闪】的 USE 帧出栈后，由上一条 ``canceled`` 分支决定去向。
    return "stay"


def resolve_effect(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.RESOLVE_EFFECT, actor=_effect_actor(frame))


def resolve_after_effect(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.RESOLVE_AFTER_EFFECT, actor=_effect_actor(frame))
    host.apply_effects(
        frame, behavior_for(frame.data["card"]).effects, timing=Timing.RESOLVE_AFTER_EFFECT
    )


def resolve_end(host: FrameHost, frame: Frame) -> None:
    _emit(host, frame, Timing.RESOLVE_END, actor=_effect_actor(frame))


USE_STEPS = (
    Step(use_choose_target, Timing.USE_CHOOSE_TARGET),
    Step(use_on_use, Timing.USE_ON_USE),
    Step(use_designate_target, Timing.USE_DESIGNATE_TARGET),
    Step(use_become_target, Timing.USE_BECOME_TARGET),
    Step(use_after_designate_target, Timing.USE_AFTER_DESIGNATE_TARGET),
    Step(use_after_become_target, Timing.USE_AFTER_BECOME_TARGET),
    # 压栈那一步的 `timing` 只作子帧 `return_to` 的可读标签（票 04 §4）：
    # RESOLVE_START 由 RESOLVE 帧自己 dispatch，不该记在 USE 帧的 id 上。
    Step(use_push_resolve, Timing.RESOLVE_START),
    Step(use_after_end, Timing.RESOLVE_AFTER_END),
)

RESOLVE_STEPS = (
    Step(resolve_start, Timing.RESOLVE_START),
    Step(resolve_before_effect, Timing.RESOLVE_BEFORE_EFFECT),
    Step(resolve_effect, Timing.RESOLVE_EFFECT),
    Step(resolve_after_effect, Timing.RESOLVE_AFTER_EFFECT),
    Step(resolve_end, Timing.RESOLVE_END),
)


def _pc_of(timing: Timing) -> int:
    """按**时机**（而不是硬编码下标）定位步骤，重排步骤表不会再让跳转错位。"""
    return next(index for index, step in enumerate(RESOLVE_STEPS) if step.timing is timing)


RESOLVE_END_PC = _pc_of(Timing.RESOLVE_END)
"""RESOLVE 步骤表里"使用结算结束时"的位置：被抵消时 Goto 到这里（跳过生效时 / 生效后）。"""


def build_steps() -> StepTable:
    return {
        FrameKind.USE: USE_STEPS,
        FrameKind.RESOLVE: RESOLVE_STEPS,
        FrameKind.DAMAGE: (),
        FrameKind.RECOVER: (),
    }
