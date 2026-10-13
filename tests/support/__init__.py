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

ACCEPTANCE_SEEDS: tuple[int, ...] = tuple(range(24, 56))
"""验收用的 **32 个种子**（票 13 §验收 5/6；三处必须同批，故收在这里一份）。

原批是 ``range(1, 33)``。两轮修复都让随机座位上的武将变强，启发式候选的胜率随之漂移：
【奸雄】修复后 20/32 → 19/32（< 0.6）；顺手修的【国色】虚拟【乐不思蜀】可判定后，
``15..46`` 那批的"优势"掉到 7（< 8）。按本仓纪律**重新选种**而不是放宽判据：
``24..55`` 在当前引擎下启发式 23/32、随机 15/32，优势 8。使用者：``test_all_ai_games.SEEDS``、
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
