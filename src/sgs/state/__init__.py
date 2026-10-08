"""对局状态：座位、体力、区域、牌实例，以及询问 / 行动协议与参与者视角。

公开接口只在此再导出，外部不得 import 本子包的深层路径。
"""

from .deltas import (
    CardMoveDelta,
    DeckShuffleDelta,
    Delta,
    HpChangeDelta,
)
from .models import (
    CardRef,
    FrameKind,
    GameState,
    MoveReason,
    Phase,
    Player,
    Zone,
)
from .projection import project
from .protocol import (
    Action,
    DiscardCards,
    DiscardQuery,
    DodgeQuery,
    EndPhase,
    Pass,
    PeachQuery,
    PlayPhaseQuery,
    PlayerPublic,
    PlayerView,
    Query,
    QueryKind,
    UseCard,
)

__all__ = (
    "Action",
    "CardMoveDelta",
    "CardRef",
    "DeckShuffleDelta",
    "Delta",
    "DiscardCards",
    "DiscardQuery",
    "DodgeQuery",
    "EndPhase",
    "FrameKind",
    "GameState",
    "HpChangeDelta",
    "MoveReason",
    "Pass",
    "PeachQuery",
    "Phase",
    "PlayPhaseQuery",
    "Player",
    "PlayerPublic",
    "PlayerView",
    "Query",
    "QueryKind",
    "UseCard",
    "Zone",
    "project",
)
