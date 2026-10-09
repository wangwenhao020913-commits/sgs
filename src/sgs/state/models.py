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

from sgs.data import Color, Gender, Identity, RuleConfig

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
    ``attack_range`` 同为**牌面事实**（票 09 §6 / 票 12 §1）：由 ``CardType.attack_range``
    在实例化时一次派生，使 ``attack_range()`` 真读武器牌而不必回头拿内容包；非武器恒为
    ``None``。两者都是"渲染 / 校验要读的事实"，不是行为字段。

    票 30 的虚拟牌 / 转化牌：``card_id < 0`` 是虚拟牌的**唯一判据**（票 12 §Q21）。
    虚拟牌不参与牌守恒与区域互斥（它从不进任何区域），故 ``suit`` / ``rank``
    **可空**、``color`` 由花色派生或显式给出（纯虚拟牌三者皆无）。实体牌仍由
    :func:`sgs.data.color_of_suit` 在实例化时一次派生颜色，不另抄映射。
    """

    card_id: int
    type: str
    name: str
    suit: str | None = None
    rank: int | None = None
    slot: SlotKind | None = None
    attack_range: int | None = None
    color: Color | None = None
    virtual_sources: tuple[int, ...] = ()
    """转化牌的**来源实体牌 id**（票 12 §Q21 的"一条 delta 带两张"）。

    纯虚拟牌为空元组（无实体来源，如【八卦阵】视为使用/打出的【闪】）；
    转化牌（【丈八蛇矛】两张手牌当【杀】）装上被转化的实体牌 id，使用结界里据此把
    来源牌按正常使用路径移动（手牌 → 处理区 → 弃牌堆），而虚拟牌本身不落任何区域。
    """

    @property
    def is_virtual(self) -> bool:
        """虚拟牌 / 转化牌：``card_id < 0``（票 12 §Q21 的判据）。"""
        return self.card_id < 0


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

    前八个是 M2 真的会走到 `push` 的结算单元（``JUDGE`` 自票 26 从占位转真帧）；
    其余是留位（触发它们的机制在本里程碑被排除），只作 ``frame`` 记录
    ``frame_kind`` 的合法取值。
    """

    # M2 会走到
    USE = "USE"
    RESOLVE = "RESOLVE"
    DAMAGE = "DAMAGE"
    LIFE_DECREASE = "LIFE_DECREASE"
    NEAR_DEATH = "NEAR_DEATH"
    DEATH = "DEATH"
    RECOVER = "RECOVER"
    JUDGE = "JUDGE"
    # 留枚举位
    PLAY = "PLAY"
    MOVE = "MOVE"
    LIFE_LOSE = "LIFE_LOSE"
    MAXLIFE = "MAXLIFE"
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
    gender: Gender | None = None
    """座位级性别（票 30 / 票 05 §7.1 的【雌雄双股剑】判据）。

    性别是**武将牌**的事实；M2 无武将 ⇒ 恒 ``None``（"未知"），"异性"判据因此不成立。
    字段是 M3 的接口位：加武将时由武将数据填值，装备效果的形状不变。
    """
    virtual_equipment: tuple[str, ...] = ()
    """**虚拟装备**（"视为装备"）的持久授予态：授予它的技能 id 元组（票 07 §6 / 票 30）。

    虚拟装备**不是牌**，绝不进装备区、不占牌守恒（票 07 §6 的"绝不为它造 CardRef"）；
    `装备区` 只放实体牌，`已装备` = 实体装备 ∪ 虚拟授予（由 ``skills.is_equipped`` 求并集）。
    M2 没有生产者（【八阵】类技能属 M3），字段是接口位。
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
    skill_state: dict[str, int] = Field(default_factory=dict)
    """**per-instance 技能状态**（票 07 §6 / 票 30）：实例键 → 计数（如"已用次数"）。

    实例键 = ``(owner, skill_id, source)`` 的规范文本（``skills.instance_key``）；
    来源消失（装备离场）时由 ``skills.prune_skill_state`` 清理。M2 的 17 条装备没有
    持久状态，故本字段实际恒空——它是"M3 只许增"的接口位，形状在此钉死。
    """

    def player(self, seat: int) -> Player:
        return self.players[seat]

    def card(self, card_id: int) -> CardRef:
        """按 ``card_id`` 取牌面；虚拟牌（``card_id < 0``）不在 ``cards`` 里，取它即编程错误。

        负下标若被静默放行会取到牌堆末尾的另一张实体牌（Python 负索引），那会静默破坏
        "虚拟牌不参与牌守恒"这条不变量，故这里 fail-closed。
        """
        if card_id < 0:
            raise ValueError(f"牌 {card_id} 是虚拟牌，不在实体牌表里")
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
