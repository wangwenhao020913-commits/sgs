"""测试支撑：构造局面的纯函数与脚本玩家（票 12 §6）。"""
from .positions import (
    PeachInPlayPhasePlayer,
    RecordingPlayer,
    SeatSpec,
    SequenceHalt,
    build_state,
    cards_of_type,
    play_phase_peach_engine,
    play_standard,
    run_turns,
    run_until,
    turn_loop_engine,
)

__all__ = (
    "PeachInPlayPhasePlayer",
    "RecordingPlayer",
    "SeatSpec",
    "SequenceHalt",
    "build_state",
    "cards_of_type",
    "play_phase_peach_engine",
    "play_standard",
    "run_turns",
    "run_until",
    "turn_loop_engine",
)
