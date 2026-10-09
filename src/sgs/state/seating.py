"""座位环：只读真源（票 15 §1 / 票 23）。

座位环回答三类问题：谁在环上（存活座位序）、某个座位的下一个存活者是谁、
从某座位起逆时针的一段区间里有哪些存活者。``distance()`` / 回合轮转 / 应答者区间 /
延时锦囊顺延 / M5 传导都只从这里读，不各自重算一遍"逆时针 = 座位号 +1"。

本模块是**纯函数**、不改状态，也不放进状态模型（``GameState`` / ``Player`` 一个字段
都不加）：真源仍是 ``players`` 的座位序，本模块只是它的唯一读取面（票 23 落点）。
"""

from __future__ import annotations

from .models import GameState

__all__ = ["alive_seat_at", "alive_seats", "next_alive_seat", "rejudge_askers", "seats_from"]


def alive_seats(state: GameState) -> tuple[int, ...]:
    """存活者的座位序（按座位号升序）。"""
    return tuple(player.seat for player in state.players if player.alive)


def alive_seat_at(state: GameState, index: int) -> int | None:
    """第 ``index`` 个存活座位（0 基）；越界为 ``None``。"""
    seats = alive_seats(state)
    if 0 <= index < len(seats):
        return seats[index]
    return None


def next_alive_seat(state: GameState, seat: int) -> int | None:
    """``seat`` 逆时针方向（座位号 +1、绕环）的下一个存活座位。

    绝不返回 ``seat`` 本人：除它之外没有存活者时返回 ``None``。终局条件（存活 ≤ 1）
    先于回合轮转成立，故正常对局读不到 ``None``（票 15 §6）。
    """
    count = len(state.players)
    for offset in range(1, count):
        candidate = (seat + offset) % count
        if state.player(candidate).alive:
            return candidate
    return None


def seats_from(state: GameState, start: int | None, count: int) -> tuple[int, ...]:
    """从 ``start`` 起逆时针、沿座位环走 ``count`` 个座位、只留存活者。

    这是**应答者区间**的座位环读法（票 15 §2）：``start`` = 当前回合角色、
    ``count`` = 座位总数时，区间恰是"从当前回合角色起绕整圈、终点为其上家"。
    ``start`` 为 ``None`` 时退化为座位 0 起（M1 保留的结构分支）。
    走超过一圈不重复数人，故区间长度恒 ≤ 存活数。
    """
    total = len(state.players)
    if total <= 0 or count <= 0:
        return ()
    origin = 0 if start is None else start % total
    return tuple(
        state.player((origin + offset) % total).seat
        for offset in range(min(count, total))
        if state.player((origin + offset) % total).alive
    )


def rejudge_askers(state: GameState, capable: tuple[int, ...]) -> tuple[int, ...]:
    """**改判响应队列**（票 10 §4）：从当前回合角色起逆时针、每角色一次。

    ``capable`` = 有改判能力的座位（技能侧解析，M2 恒空）。区间读法与濒死求桃同源
    （``seats_from`` 的"当前回合角色起绕整圈"），只把没有能力的座位滤掉；队列为空时
    判定帧不构造任何询问——这就是"队列恒空即终态正确"。
    """
    return tuple(
        seat
        for seat in seats_from(state, state.turn_seat, len(state.players))
        if seat in capable
    )
