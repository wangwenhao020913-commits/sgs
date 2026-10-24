"""**事件帧无效**（M6 票 08；spec D15 四层否定的第三层）。

规则源：`三国杀详细规则.txt` §十九 L2103–L2110（界黄月英【奇才】②"当其他角色弃置你装备区
里的防具牌和宝物牌时，取消之"）。

四层否定**各自实现、不共用类型**（D15 / 票 07 的审计清单）：本模块只装**事件帧无效**这一层
的机制符号 :data:`EVENT_FRAME_INVALID`——一个**事件帧自己**的帧私有失效标志。语义是
"事件已经发生（帧已经开了、载荷已经写进帧），在它自己的帧里被取消"：

- **写入口**：技能层（:func:`invalidate_event_frame`）。
- **读取口**：事件自己的执行点（引擎在派发技能之后读一次，见
  ``Engine.move_in_frame`` 的"装备区 → 弃牌堆"分支）。
- **生存期**：帧私有、`take` 一次即消费。它**不进记录流**（帧私有数据不落盘），故标准
  47 条金录像逐字节不变。

与其余三层的分界（票 07 已登记）：目标产生前取消在 ``target_pool``；目标级无效是
:mod:`sgs.skills.negation`；响应窗口不产生是帧标签 ``Tag.UNRESPONSIVE``。本层不复用它们
的任何符号——`tests/test_negation_sites.py` 的"四层机制符号两两不交"钉着这条。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "EVENT_FRAME_INVALID",
    "invalidate_event_frame",
    "take_event_frame_invalid",
]

EVENT_FRAME_INVALID = "event_frame_invalid"
"""帧私有槽名：这个**事件帧**已经被取消（布尔；消费一次即清）。"""


def invalidate_event_frame(frame: Any) -> None:
    """把当前事件帧标成**无效**（幂等；由技能层在确认条件成立后调用）。

    写入是帧私有数据（``frame.data``）——不落记录流、不发 delta，故"取消"这件事只体现为
    **那条移动没有发生**（没有 ``card_move``、没有它引发的 ``MOVE_AFTER``）。
    """
    frame.data[EVENT_FRAME_INVALID] = True


def take_event_frame_invalid(frame: Any) -> bool:
    """读取并**消费**当前帧的失效标志：``True`` = 这个事件帧被取消。

    "消费"是刻意的：同一帧可能连续跑几件事（拆两处 / 判定收尾），失效只属于**当前这一件**；
    标志留着会让下一件同类事件被误取消。没有标志时是零动作。
    """
    return bool(frame.data.pop(EVENT_FRAME_INVALID, False))
