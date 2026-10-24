"""变化事件（delta）的载荷类型：一次状态变化的纯事实（票 06 §2 / §3）。

``delta_type`` 是记录流 schema 的一部分，后补会动事件版本，所以即使 M1 的骨架期只用得到
``card_move`` 也先建位。现有 11 种：M1 的八种（``card_move`` / ``hp_change`` /
``chained_change`` / ``wine_change`` / ``card_reveal`` / ``deck_shuffle`` /
``identity_deal`` / ``identity_reveal``）之后，``chained_change``（M5-03 横置）、
``wine_change``（M5-04 酒）与 ``card_reveal``（M5-06 火攻亮牌）都是就地追加；M6 票 04 再
追加技能三态的 ``skill_gain`` / ``skill_lose`` / ``skill_suppress``（技能获得 / 失去 /
失效），记录流仍为事件版本 2。记录层
（``engine``）在这些模型上**继承**出带 ``seq`` / ``frame_id`` / ``depth`` 的记录子类，
从而 JSON 是平铺的。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from sgs.data import Identity

from .models import (
    CardRef,
    MoveReason,
    SkillSuppressionPredicate,
    SkillSuppressionScope,
    ZoneRef,
    _FrozenModel,
)

__all__ = [
    "CardMoveDelta",
    "CardRevealDelta",
    "ChainedChangeDelta",
    "DeckShuffleDelta",
    "Delta",
    "HpChangeDelta",
    "IdentityDealDelta",
    "IdentityRevealDelta",
    "SkillGainDelta",
    "SkillLoseDelta",
    "SkillSuppressDelta",
    "WineChangeDelta",
]


class CardMoveDelta(_FrozenModel):
    """一批实体牌从**一个**来源区域移动到目标区域（一次操作一条）。

    ``from_zone`` / ``to_zone`` 是**区域坐标**（区种类 + 座位）：一次弃置两张不同来源的牌
    是两条 delta，故每条只有一个来源坐标。``owner`` 是**归属**——"本次移动算谁的"
    （判定牌 = 被判定者、弃置 / 使用 = 弃置者 / 使用者、摸牌 = 摸牌者、装备置入 = 持有者）；
    只有"无角色主体"的移动（如终局收尾）为 ``None``。归属 ≠ 区域坐标：判定区里的牌
    坐标带座位，但牌本身仍不为任何角色所拥有（规则 11.6）。
    """

    delta_type: Literal["card_move"] = "card_move"
    cards: tuple[CardRef, ...]
    from_zone: ZoneRef
    to_zone: ZoneRef
    reason: MoveReason
    owner: int | None = None
    """本次移动的归属角色；"无角色主体"时为 ``None``。"""


class HpChangeDelta(_FrozenModel):
    """体力被写成某个值（``apply_hp`` 是唯一改体力的口）。"""

    delta_type: Literal["hp_change"] = "hp_change"
    seat: int
    before: int
    after: int
    amount: int


class ChainedChangeDelta(_FrozenModel):
    """一个座位的**横置**状态被写成某个值（M5-03 / 决策 D5）。

    写入口唯一：``Engine.set_chained``。铁索连环的 toggle 与属性伤害的"结算后立即重置"
    都经它发这条 delta——重置因此发生在**同一伤害事件内**（记录流里与那条
    ``hp_change`` 同帧），不另开事件帧，也不产生第二个时机。
    """

    delta_type: Literal["chained_change"] = "chained_change"
    seat: int
    chained: bool


class WineChangeDelta(_FrozenModel):
    """一个座位的【酒】加伤状态被写成某个值（M5-04 / 决策 D5）。

    写入口唯一：``Engine.set_wine_active``。出牌阶段使用【酒】把它写成 ``True``；兑现给
    下一张【杀】的使用、以及回合末清理都经它写成 ``False``。与 ``chained_change`` 同形
    （只记**新值**：``active``），因为它同样是"回合内的临时状态 + 网页座位卡读投影"。

    加伤只在本回合有效，故它不会跨回合出现在记录流里；标准 108 无【酒】，故标准基准
    一条都不会出现这条 delta。
    """

    delta_type: Literal["wine_change"] = "wine_change"
    seat: int
    active: bool


class CardRevealDelta(_FrozenModel):
    """一张**手牌**成为公开事实（M5-06 的【火攻】亮牌 / 决策 D10）。

    只揭示、不移动：亮出的牌仍留在原区（火攻亮出的手牌留在手里，不离开手牌区）。
    因此它**不是** ``card_move``——不进牌守恒的搬运记账，只回答"哪张牌此刻被公开了"。
    ``seat`` = 亮出者（手牌持有者），``card`` = 被公开的那一张。

    **不持久**：不写任何"已知"状态（手牌区不留标记），记录流里的这一条就是全部公开
    事实；未亮出的手牌一张都不在这里出现。标准 108 无【火攻】，故 47 条金录像一条都
    不会出现这条 delta。
    """

    delta_type: Literal["card_reveal"] = "card_reveal"
    seat: int
    card: CardRef


class DeckShuffleDelta(_FrozenModel):
    """一次洗牌置换；``cards`` 是**洗后**自顶向下的牌堆顺序，不记洗前顺序。

    洗牌**无归属**（无角色主体）；区域引用与 ``card_move`` 同形，一并升为区域坐标。
    """

    delta_type: Literal["deck_shuffle"] = "deck_shuffle"
    cards: tuple[CardRef, ...]
    from_zone: ZoneRef | None
    """初次洗牌为 ``None``；重洗为 ``{"zone": "discard", "seat": null}``。"""

    to_zone: ZoneRef


class IdentityDealDelta(_FrozenModel):
    """一个座位被发到身份（票 13 §3）：逐座一条、落在发牌**之前**、不新增时机节点。

    身份不是牌：不进 ``cards``、不占牌守恒、与 :class:`MoveReason` 无关。
    """

    delta_type: Literal["identity_deal"] = "identity_deal"
    seat: int
    identity: Identity


class IdentityRevealDelta(_FrozenModel):
    """一个座位阵亡时亮明身份（票 25 / 决策回票 14 §5）：死亡结算内、检测之前逐座一条。

    与 :class:`IdentityDealDelta` 同形但语义不同——发放是开局**暗发**（逐座落在发牌前），
    亮明是阵亡**公开**（`DEATH_REVEAL_IDENTITY_BEFORE` 的时机 dispatch 之后）。身份不是牌：
    不进 ``cards``、不占牌守恒，与 :class:`MoveReason` 无关。
    """

    delta_type: Literal["identity_reveal"] = "identity_reveal"
    seat: int
    identity: Identity


class SkillGainDelta(_FrozenModel):
    """一个座位**获得**了一条技能实例（M6 票 04 的第四种来源）。

    技能不是牌：不进 ``cards``、不占牌守恒、与 :class:`MoveReason` 无关。``skill_id`` 是
    被授予的**技能定义 id**；来源种类恒为 ``GRANTED``（实例本身由载体解析按座位状态重建，
    故 delta 只记"谁获得了哪条"，不重复来源形状）。
    """

    delta_type: Literal["skill_gain"] = "skill_gain"
    seat: int
    skill_id: str


class SkillLoseDelta(_FrozenModel):
    """一个座位**失去**了一条技能（M6 票 04）：实例不再存在，且不可恢复。

    与 :class:`SkillGainDelta` 同形；"失去 ≠ 失效"由两个 delta 类型分开表达
    （`GLOSSARY.md`：失效可恢复、失去不可恢复）。
    """

    delta_type: Literal["skill_lose"] = "skill_lose"
    seat: int
    skill_id: str


class SkillSuppressDelta(_FrozenModel):
    """一个座位的**失效**记录被加上 / 撤掉（M6 票 04）。

    ``skill_id`` = **施加失效的那条技能**（不是被挡住的技能）：失效率是座位级的，
    一条记录覆盖整个座位，逐技能展开会写出与规则无关的噪声。``suppressed=False`` 是
    作用域到期（回合末）那一条，使"可恢复"在记录流里可被看见。
    """

    delta_type: Literal["skill_suppress"] = "skill_suppress"
    seat: int
    skill_id: str
    suppressed: bool
    scope: SkillSuppressionScope = SkillSuppressionScope.TURN
    predicate: SkillSuppressionPredicate = SkillSuppressionPredicate.ALL_NON_LOCKED


Delta = Annotated[
    CardMoveDelta
    | HpChangeDelta
    | ChainedChangeDelta
    | WineChangeDelta
    | CardRevealDelta
    | DeckShuffleDelta
    | IdentityDealDelta
    | IdentityRevealDelta
    | SkillGainDelta
    | SkillLoseDelta
    | SkillSuppressDelta,
    Field(discriminator="delta_type"),
]
