"""对局状态：玩家、牌实例、区域与结算栈的帧类型。

状态只用**有序容器**（``list`` / ``tuple`` / ``dict``，``dict`` 按插入序）：
``set`` / ``frozenset`` 不得作为字段出现——它们的迭代序逐进程变化，会破坏
"同种子逐字节复现"（票 08 §7）。牌实例的 ``card_id`` 就是 ``cards`` 里的下标，
一旦开局分配就不再变化；洗牌只置换区域列表。

本模块只装**状态**：行为字段（使用时机 / 目标规则 / 次数限制 / 效果列表）归 ``effects``，
记录流 schema 归 ``engine``。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from sgs.data import RuleConfig

__all__ = [
    "CardRef",
    "FrameKind",
    "GameState",
    "MoveReason",
    "Phase",
    "Player",
    "Zone",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CardRef(_FrozenModel):
    """内联的牌面快照（票 06 §3）。

    ``card_id`` 是机器键；牌面（牌名 / 花色 / 点数）是渲染所需的事实，不是表现文本。
    M1 无转化牌与虚拟牌，内联不会漂移。
    """

    card_id: int
    type: str
    name: str
    suit: str
    rank: int


class Zone(str, Enum):
    """实体牌所在的区域。M1 只有这四个（判定区 / 装备区 / 武将牌上属 M2）。"""

    DECK = "deck"
    HAND = "hand"
    DISCARD = "discard"
    PROCESSING = "processing"


class MoveReason(str, Enum):
    """**来源**：牌实例移动到当前区域的操作原因。

    官方清单只界定**角色**失去牌的原因；牌堆方向的移动（摸牌）须另列。
    洗牌走 ``delta_type = deck_shuffle``，**不**占这里的取值（票 08 §5）。
    """

    DRAW = "DRAW"
    USE = "USE"
    DISCARD = "DISCARD"


class Phase(str, Enum):
    """回合的六个阶段（票 01 L2）。"""

    PREPARE = "prepare"
    JUDGE = "judge"
    DRAW = "draw"
    PLAY = "play"
    DISCARD = "discard"
    FINISH = "finish"


class FrameKind(str, Enum):
    """结算事件的帧类型（票 04 §6）。

    前七个是 M1 真的会走到 `push` 的结算单元；其余是留位（触发它们的机制被排除），
    只作 ``frame`` 记录 ``frame_kind`` 的合法取值。
    """

    # M1 会走到
    USE = "USE"
    RESOLVE = "RESOLVE"
    DAMAGE = "DAMAGE"
    LIFE_DECREASE = "LIFE_DECREASE"
    NEAR_DEATH = "NEAR_DEATH"
    DEATH = "DEATH"
    RECOVER = "RECOVER"
    # 留枚举位
    PLAY = "PLAY"
    MOVE = "MOVE"
    LIFE_LOSE = "LIFE_LOSE"
    MAXLIFE = "MAXLIFE"
    JUDGE = "JUDGE"
    RANK_COMPARE = "RANK_COMPARE"
    SKILL = "SKILL"


class Player(BaseModel):
    """一个座位。``seat`` 就是它在 :attr:`GameState.players` 里的下标。"""

    model_config = ConfigDict(extra="forbid")

    seat: int
    hp: int
    hp_max: int
    alive: bool = True
    hand: list[int] = Field(default_factory=list)
    identity: str | None = None
    """M1 无身份局：字段保留在状态里，但**不参与任何判定**（票 13 §3）。"""


class GameState(BaseModel):
    """一局的全部棋盘状态。

    ``cards`` 里第 ``i`` 个牌实例的 ``card_id`` 恒为 ``i``；区域字段装的是
    ``card_id``（有序：``deck`` 顶端在前、``discard`` 先入在前、``hand`` 按持有顺序）。
    """

    model_config = ConfigDict(extra="forbid")

    rule_config: RuleConfig
    pack_id: str
    deck_profile: str
    cards: tuple[CardRef, ...] = ()
    players: tuple[Player, ...] = ()
    deck: list[int] = Field(default_factory=list)
    discard: list[int] = Field(default_factory=list)
    processing: list[int] = Field(default_factory=list)
    turn_seat: int | None = None
    phase: Phase | None = None

    def player(self, seat: int) -> Player:
        return self.players[seat]

    def card(self, card_id: int) -> CardRef:
        return self.cards[card_id]

    def refs(self, card_ids: tuple[int, ...] | list[int]) -> tuple[CardRef, ...]:
        return tuple(self.cards[card_id] for card_id in card_ids)

    def zone(self, zone: Zone) -> list[int]:
        """区域名 → 该区域的 ``card_id`` 列表（唯一入口，原语只经它改区域）。"""
        if zone is Zone.DECK:
            return self.deck
        if zone is Zone.HAND:
            raise ValueError("手牌按座位分装，请用 player(seat).hand")
        if zone is Zone.DISCARD:
            return self.discard
        if zone is Zone.PROCESSING:
            return self.processing
        raise ValueError(f"未知区域：{zone!r}")
