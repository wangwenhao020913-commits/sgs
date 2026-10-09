"""效果原语与牌行为表。

公开接口只在此再导出，外部不得 import 本子包的深层路径。
"""

from .actions import (
    attack_range,
    check_count,
    check_forbidden,
    check_targets,
    discard_count,
    distance,
    hand_limit,
    legal_actions_for,
)
from .behaviors import (
    CARD_BEHAVIORS,
    CallOp,
    CardBehavior,
    DamageNature,
    EffectStep,
    FinalZone,
    PushFrame,
    TargetCount,
    TargetRule,
    UseContext,
    UseLimit,
    UseTiming,
    behavior_for,
    opens_response_window,
)
from .ops import OPS
from .primitives import (
    DrawResult,
    EffectContext,
    Randomness,
    apply_hp,
    draw_from_deck,
    locate,
    move_card,
)

__all__ = (
    "CARD_BEHAVIORS",
    "OPS",
    "CallOp",
    "CardBehavior",
    "DamageNature",
    "DrawResult",
    "EffectContext",
    "EffectStep",
    "FinalZone",
    "PushFrame",
    "Randomness",
    "TargetCount",
    "TargetRule",
    "UseContext",
    "UseLimit",
    "UseTiming",
    "apply_hp",
    "attack_range",
    "behavior_for",
    "check_count",
    "check_forbidden",
    "check_targets",
    "discard_count",
    "distance",
    "draw_from_deck",
    "hand_limit",
    "legal_actions_for",
    "locate",
    "move_card",
    "opens_response_window",
)
