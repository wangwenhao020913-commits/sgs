"""测试支撑：构造局面的纯函数与脚本玩家（票 12 §6）。"""
from .positions import RecordingPlayer, SeatSpec, build_state, cards_of_type, play_standard

__all__ = (
    "RecordingPlayer",
    "SeatSpec",
    "build_state",
    "cards_of_type",
    "play_standard",
)
