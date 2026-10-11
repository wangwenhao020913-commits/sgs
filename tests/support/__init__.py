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

__all__ = (
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
