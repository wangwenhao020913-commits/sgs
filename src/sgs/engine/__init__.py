"""引擎门面、随机源与记录流。

公开接口只在此再导出，外部不得 import 本子包的深层路径。
"""

from .errors import IllegalActionError, JournalVersionError
from .game import DRAW_COUNT, INITIAL_HP, Engine, Outcome, instantiate_cards, new_game
from .journal import (
    EVENT_SCHEMA_VERSION,
    SUPPORTED_EVENT_VERSIONS,
    ActionRecord,
    CardMoveRecord,
    DeckShuffleRecord,
    DeltaRecord,
    FrameRecord,
    GameEndRecord,
    Header,
    HpChangeRecord,
    JournalRecord,
    MalformedAction,
    QueryRecord,
    TimingRecord,
    dump_journal,
    read_journal,
    record_json,
    wrap_delta,
)
from .rng import SUBSTREAM_DOMAIN, RandomSource, new_seed

__all__ = (
    "DRAW_COUNT",
    "EVENT_SCHEMA_VERSION",
    "INITIAL_HP",
    "SUBSTREAM_DOMAIN",
    "SUPPORTED_EVENT_VERSIONS",
    "ActionRecord",
    "CardMoveRecord",
    "DeckShuffleRecord",
    "DeltaRecord",
    "Engine",
    "FrameRecord",
    "GameEndRecord",
    "Header",
    "HpChangeRecord",
    "IllegalActionError",
    "JournalRecord",
    "JournalVersionError",
    "MalformedAction",
    "Outcome",
    "QueryRecord",
    "RandomSource",
    "TimingRecord",
    "dump_journal",
    "instantiate_cards",
    "new_game",
    "new_seed",
    "read_journal",
    "record_json",
    "wrap_delta",
)
