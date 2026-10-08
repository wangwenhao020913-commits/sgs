"""参与者：脚本玩家、随机玩家与回放执行者。

公开接口只在此再导出，外部不得 import 本子包的深层路径。
"""

from .participants import Participant, RandomPlayer, TapeError, TapePlayer, tape_from_journal

__all__ = (
    "Participant",
    "RandomPlayer",
    "TapeError",
    "TapePlayer",
    "tape_from_journal",
)
