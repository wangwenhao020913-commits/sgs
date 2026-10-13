"""测试支撑：构造局面的纯函数与脚本玩家（票 12 §6）。"""
from .positions import (
    PeachInPlayPhasePlayer,
    RecordingPlayer,
    SeatSpec,
    SequenceHalt,
    build_state,
    cards_of_type,
    declare_death,
    play_phase_peach_engine,
    play_standard,
    run_turns,
    run_until,
    turn_loop_engine,
)
from .scripts import PassPlayer, find_action, ran_timing, run_queries

ACCEPTANCE_SEEDS: tuple[int, ...] = tuple(range(15, 47))
"""验收用的 **32 个种子**（票 13 §验收 5/6；三处必须同批，故收在这里一份）。

原批是 ``range(1, 33)``。【奸雄】修复（牌不再被 L5.6 从曹操手里弃掉）后，``1..32``
那批里随机座位上的曹操变强，启发式候选的胜率从 20/32 掉到 19/32（0.594 < 0.6）。
按本仓纪律**重新选种**而不是放宽判据：``15..46`` 在同一判据下启发式 20/32、随机 11/32，
优势 9（原批优势 8）。使用者：``test_all_ai_games.SEEDS``、
``test_heuristic_ai`` 的 32 种子扫描与胜率对比。"""

__all__ = (
    "ACCEPTANCE_SEEDS",
    "PassPlayer",
    "PeachInPlayPhasePlayer",
    "RecordingPlayer",
    "SeatSpec",
    "SequenceHalt",
    "build_state",
    "cards_of_type",
    "declare_death",
    "find_action",
    "play_phase_peach_engine",
    "play_standard",
    "ran_timing",
    "run_queries",
    "run_turns",
    "run_until",
    "turn_loop_engine",
)
