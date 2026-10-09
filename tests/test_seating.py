"""座位环：只读真源（票 23）。

落点 = **纯函数直接单测**（票 15 §1 的落点；不是第五条缝，照 ``distance()`` 先例）。
座位环是距离 / 回合轮转 / 应答者区间 / 延时锦囊顺延共用的唯一读法，故它自己的
边界（死者在环外、绕环、越界、区间终点）单独钉死。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.state import alive_seat_at, alive_seats, next_alive_seat, seats_from

from support import SeatSpec, build_state

PACK = load_content_pack(deck_profile="standard_108")


def _state(*alive: bool):
    return build_state(
        pack=PACK,
        seats=[SeatSpec(alive=flag) for flag in alive],
        deck=tuple(range(10)),
        turn_seat=0,
    )


def test_alive_seats_is_the_survivor_ring_in_seat_order():
    state = _state(True, False, True, True)

    assert alive_seats(state) == (0, 2, 3)


def test_alive_seat_at_reads_the_ith_survivor_and_none_outside_the_ring():
    state = _state(True, False, True, True)

    assert alive_seat_at(state, 0) == 0
    assert alive_seat_at(state, 1) == 2
    assert alive_seat_at(state, 2) == 3
    assert alive_seat_at(state, 3) is None
    assert alive_seat_at(state, -1) is None


def test_next_alive_seat_skips_the_dead_and_wraps_counterclockwise():
    state = _state(True, False, True, False)

    assert next_alive_seat(state, 0) == 2, "座位 1 阵亡 ⇒ 下家是座位 2"
    assert next_alive_seat(state, 2) == 0, "环尾绕回座位 0"
    assert next_alive_seat(state, 1) == 2, "从死座位起也能读到它之后的下一个活人"


def test_next_alive_seat_is_none_when_no_other_seat_survives():
    state = _state(True, False)

    assert next_alive_seat(state, 0) is None


def test_seats_from_walks_the_whole_ring_and_filters_the_dead():
    state = _state(True, True, False, True)

    assert seats_from(state, 0, 4) == (0, 1, 3), "绕整圈、死者不进区间"
    assert seats_from(state, 3, 4) == (3, 0, 1), "起点在环尾时照常绕回"


def test_a_dead_start_seat_falls_through_to_its_next_survivor():
    """票 23 "不属于任何角色的回合"：起点座位已阵亡时，区间首个实际应答者 = 其下家。"""
    state = _state(True, False, True, True)

    assert seats_from(state, 1, 4) == (2, 3, 0)


def test_seats_from_stops_at_the_turn_players_previous_seat():
    """应答者区间：起点 = 当前回合角色，终点 = 其上家（绕整圈）。"""
    state = _state(True, True, True, True)

    interval = seats_from(state, 2, 4)

    assert interval == (2, 3, 0, 1)
    assert interval[-1] == 1, "座位 2 的上家 = 座位 1"


def test_seats_from_treats_a_missing_start_as_seat_zero_and_caps_the_walk():
    state = _state(True, True, True)

    assert seats_from(state, None, 3) == (0, 1, 2), "M1 保留的退化分支"
    assert seats_from(state, 0, 99) == (0, 1, 2), "走超过一圈不重复数人"
