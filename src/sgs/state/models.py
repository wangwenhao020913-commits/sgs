"""对局状态：玩家、牌实例、区域与结算栈的帧类型。

状态只用**有序容器**（``list`` / ``tuple`` / ``dict``，``dict`` 按插入序）：
``set`` / ``frozenset`` 不得作为字段出现——它们的迭代序逐进程变化，会破坏
"同种子逐字节复现"（票 08 §7）。牌实例的 ``card_id`` 就是 ``cards`` 里的下标，
一旦开局分配就不再变化；洗牌只置换区域列表。

本模块只装**状态**：行为字段（使用时机 / 目标规则 / 次数限制 / 效果列表）归 ``effects``，
记录流 schema 归 ``engine``。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from sgs.data import Identity, RuleConfig

__all__ = [
    "CardRef",
    "FrameKind",
    "GameState",
    "MoveReason",
    "Phase",
    "Player",
    "SlotKind",
    "Zone",
    "ZoneRef",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SlotKind(str, Enum):
    """装备区里的五个槽位（票 08 §2）；取值与数据层的 ``subcategory`` 同形。

    宝物槽位属界限突破，**预留不启用**（ADR-006）；M2 只可能见到前四个。
    """

    WEAPON = "weapon"
    ARMOR = "armor"
    HORSE_DEFENSE = "horse_defense"
    HORSE_OFFENSE = "horse_offense"
    TREASURE = "treasure"


class CardRef(_FrozenModel):
    """内联的牌面事实（票 06 §3）。

    ``card_id`` 是机器键；牌面（牌名 / 花色 / 点数）是渲染所需的事实，不是表现文本。
    ``slot`` 是装备槽位这一**牌面事实**（票 08 §2）：由 ``CardType.subcategory`` 在实例化时
    一次派生，使移动原语不必拿到 ``ContentPack``。非装备牌恒为 ``None``。
    """

    card_id: int
    type: str
    name: str
    suit: str
    rank: int
    slot: SlotKind | None = None


class Zone(str, Enum):
    """实体牌所在的区域种类（票 08 §1）。

    ``HAND`` / ``EQUIP`` / ``JUDGE`` 是**逐座位**的区，座位在 :class:`ZoneRef` 坐标里；
    ``DECK`` / ``DISCARD`` / ``PROCESSING`` 是共享区。
    """

    DECK = "deck"
    HAND = "hand"
    DISCARD = "discard"
    PROCESSING = "processing"
    EQUIP = "equip"
    JUDGE = "judge"


@dataclass(frozen=True, slots=True)
class ZoneRef:
    """**区域坐标**（票 08 §1）：区种类 + 座位。

    ``seat`` 只对 ``HAND`` / ``EQUIP`` / ``JUDGE`` 有意义，共享区为 ``None``。
    坐标说的是"**区域**在谁那里"，不是"牌归谁"——判定区的牌坐标带座位，但牌本身
    仍"不为任何角色所拥有"（规则 11.6）；"归谁"由 :class:`CardMoveDelta.owner` 回答。

    冻结值类型（用 ``dataclass`` 而非 pydantic）：pydantic 把 stdlib dataclass 字段
    序列化成 ``{"zone": ..., "seat": ...}``（``seat`` 显式写出，含 ``null``），
    与 ``CardRef`` 的平铺形状一致，且允许位置构造 ``ZoneRef(Zone.HAND, 0)``。
    """

    zone: Zone
    seat: int | None = None


class MoveReason(str, Enum):
    """**来源**：牌实例移动到当前区域的操作原因（票 08 §4）。

    官方清单只界定**角色**失去牌的原因；牌堆方向的移动（摸牌）须另列。
    洗牌走 ``delta_type = deck_shuffle``，**不**占这里的取值。
    """

    DRAW = "DRAW"
    USE = "USE"
    DISCARD = "DISCARD"
    PUT = "PUT"
    """置入：手牌 / 牌堆 → 判定区或装备区（`17.10` B1"置入相应区域"）。"""
    JUDGE = "JUDGE"
    """判定：牌堆 → 处理区 → 弃牌堆（亮判定牌与判定牌生效后入弃牌堆，`11.6`）。"""
    LOSE = "LOSE"
    """失去：判定区 / 装备区 → 弃牌堆或他处（替换失去旧装备、拆 / 顺，`4.10` / `11.5`）。"""


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
    equip: list[int] = Field(default_factory=list)
    """装备区（一个整体区）；列表序 = 槽位枚举序（票 12 §Q2），每槽至多一张。"""
    judge: list[int] = Field(default_factory=list)
    """判定区（**先入在前**）；"最后置入"的读取端用 ``reversed()`` 表达（票 08 §8）。"""
    card_use_counts: dict[str, int] = Field(default_factory=dict)
    """本回合各牌名（card type id）的已用次数；该座位自己的 ``TURN_START`` 重置（票 07 §6）。

    只存总已用次数：额定 / 额外两个上限记在行为表里，M1 的判定只用总数（票 07 §6）。
    """
    identity: Identity | None = None
    """座位级身份（票 13 §3）：无身份局恒 ``None``，身份局由 ``build_initial`` 一次发放。

    判定一律比枚举成员；主公在开局即亮明（投影侧负责可见性，票 13 §6）。
    """


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

    def zone(self, ref: ZoneRef) -> list[int]:
        """区域坐标 → 该区域的 ``card_id`` 列表（唯一入口，原语只经它改区域）。

        共享区（牌堆 / 弃牌堆 / 处理区）忽略座位；逐座位的三个区必须给出座位。
        """
        if ref.zone is Zone.DECK:
            return self.deck
        if ref.zone is Zone.DISCARD:
            return self.discard
        if ref.zone is Zone.PROCESSING:
            return self.processing
        if ref.seat is None:
            raise ValueError(f"{ref.zone.value} 区按座位分装，区域坐标必须带座位")
        player = self.player(ref.seat)
        if ref.zone is Zone.HAND:
            return player.hand
        if ref.zone is Zone.EQUIP:
            return player.equip
        if ref.zone is Zone.JUDGE:
            return player.judge
        raise ValueError(f"未知区域：{ref.zone!r}")
