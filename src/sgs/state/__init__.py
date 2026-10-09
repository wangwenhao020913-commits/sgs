"""对局状态：座位、体力、区域、牌实例，以及询问 / 行动协议与参与者视角。

公开接口只在此再导出，外部不得 import 本子包的深层路径。
"""

from .deltas import (
    CardMoveDelta,
    DeckShuffleDelta,
    Delta,
    HpChangeDelta,
    IdentityDealDelta,
)
from .models import (
    CardRef,
    FrameKind,
    GameState,
    MoveReason,
    Phase,
    Player,
    SlotKind,
    Zone,
    ZoneRef,
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
    parse_action,
)
from .seating import alive_seat_at, alive_seats, next_alive_seat, seats_from

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
    "IdentityDealDelta",
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
    "SlotKind",
    "UseCard",
    "Zone",
    "ZoneRef",
    "alive_seat_at",
    "alive_seats",
    "next_alive_seat",
    "parse_action",
    "project",
    "seats_from",
)
