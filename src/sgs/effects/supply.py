"""**需求（Demand）× 供给（Supply）**——需要（使用 / 打出）牌时的两个值对象与匹配器。

用户 2026-11-20 的提案（`spec.md` §1 逐字）把"每一档能提供什么"与"这一问要什么"变成
可比较的两件事；本模块只放**值对象 + 匹配**，不产生候选、不改任何合法性判据
（那是 need-card-supply 票 02 / 03 / 04）。三个消费点：

- :func:`supplies_for`：从 :func:`sgs.effects.actions._actions_for` 的**全档候选**反推
  逐条供给（手牌真牌 / 转化牌由牌面事实得到；提供者读 ``SkillDef.supply`` 的声明），
  **不新增真源**——不另写枚举、不复制"哪些牌能用"的判据（spec §0.1-2）；
- :func:`demand_for`：把窗口的 ``required`` + ``response_suits`` 打包成维度可加的
  :class:`Demand`；
- :func:`supply_satisfies` / :func:`supply_mismatch`：投影（``pending_payload``）用它算
  高亮 / 置灰与理由（用户 Q4：置灰**后端实时算**，前端只读 ``ok``）。

**维度可加**（用户 Q3 的落地口径）：牌名 / 花色 / 颜色 / 点数 / 虚拟·转化 / 转化源张数。
今天只有前两维有生产者（牌名 = ``required``、花色 = ``response_suits``，单骑第三段），
其余四维**同形上线、暂时恒 ``None``**；将来烈弓 / 同颜色响应只新增一个 :class:`Demand`
实例，不改框架。``None`` = 不限；``()`` = 记录为空 ⇒ 什么也满足不了（值的三态）。

**无花色 / 无点数的供给确定地不满足对应维度的限制**（不是"未知"，spec §3 第 3 条）：
【八卦阵】/【八阵】的纯虚拟【闪】与多源转化（【丈八蛇矛】/ 单骑）都没有花色 ⇒ 花色
需求下必然不满足。"候选期不知道"由 :attr:`Supply.unknown` 单独表达（【蛊惑】/【护驾】/
【激将】）：本票只标出来、**不判死**——匹配结果按"不挡"处理，真正的合法性在票 02 / 03 / 04。

循环 import（票面 §5 的纪律）：``effects/actions.py`` 与本模块互相看得见（``actions`` 的
窗口闸门与候选生成是本模块的读取方），故 ``.actions`` 与 ``sgs.skills.catalog`` 一律
**函数内惰性 import**（仓里既有姿态，见 ``actions.py:_transformed_peaches``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.state import (
    ActivateSkill,
    CardRef,
    DodgeContext,
    PeachContext,
    PlayCard,
    QueryKind,
    RespondContext,
    UseCard,
)

from .behaviors import CARD_BEHAVIORS, DODGE_RESPONSE_CARD, PEACH_RESPONSE_CARD

__all__ = [
    "Demand",
    "SUPPLY_KINDS",
    "Supply",
    "TierSupplies",
    "demand_for",
    "supplies_for",
    "supply_mismatch",
    "supply_satisfies",
]

SELF_RESCUE_CARD = "wine"
"""求桃窗口里**濒死者自救**那第二张牌名（【酒】）。

它与【桃】同档是既有事实的两处表达：真牌路径读 ``TargetRule.near_death_self_only``
（``effects/actions.py:_peach``），提供者路径读 ``skills/active.py:_GUOHUO_SELF_RESCUE_NAMES``。
需求（展示层）若只说【桃】，自救的那张【酒】会被**错误地置灰**——故这里读同一份语境事实
（``asker == dying``），牌名仍是行为表里的牌型 id，不另写"哪些牌能自救"的名单。
"""

SUPPLY_KINDS: frozenset[str] = frozenset(
    {"real", "virtual", "transformed", "relay", "preconversion"}
)
"""供给种类的闭集（票面 §1）：真牌 / 纯虚拟 / 转化 / 需求传递 / 预转化。

为什么是闭集：投影按它渲染一行人话（"实体 / 虚拟 / 转化 / 需求传递 / 预转化"），未知
种类会让渲染层静默退化成牌名 —— 与 ``sgs.effects`` 的"声明错误应当可见"同一姿态。
"""


@dataclass(frozen=True, slots=True)
class Supply:
    """候选期的一条**供给描述**：这一档此刻能提供什么样的牌。

    - ``product``：产物牌名 id（如 ``"dodge"`` / ``"slash"`` / ``"fire_slash"``）；
    - ``suit`` / ``rank`` / ``color``：牌面事实；``None`` = **恒无**（纯虚拟 / 多源转化），
      不是"不知道"——那由 :attr:`unknown` 表达（spec §3 第 3 条）；
    - ``kind``：:data:`SUPPLY_KINDS` 之一；
    - ``source_count``：转化源张数（真牌 / 纯虚拟恒 1）；
    - ``unknown``：候选期不可知（【蛊惑】的预转化 / 【护驾】/【激将】的需求传递）。
    """

    product: str | None = None
    suit: str | None = None
    rank: int | None = None
    color: str | None = None
    kind: str = "real"
    source_count: int = 1
    unknown: bool = False

    def __post_init__(self) -> None:
        if self.kind not in SUPPLY_KINDS:
            raise ValueError(f"未知的供给种类：{self.kind!r}（可选 {sorted(SUPPLY_KINDS)}）")


@dataclass(frozen=True, slots=True)
class Demand:
    """一次询问的**精确需求**，六个维度都可加（``None`` = 不限、``()`` = 记录为空）。

    今天有生产者的只有 ``names``（窗口的 ``required``）与 ``suits``（``response_suits``，
    单骑第三段）；其余四维同形上线、暂时恒 ``None``——不是死字段：构造口与匹配器现在就
    写全（用户 Q3）。
    """

    names: tuple[str, ...] | None = None
    suits: tuple[str, ...] | None = None
    colors: tuple[str, ...] | None = None
    ranks: tuple[int, ...] | None = None
    kinds: tuple[str, ...] | None = None
    source_counts: tuple[int, ...] | None = None


def _name_allowed(name: str, product: str | None) -> bool:
    """牌名维度：逐字相等，或产物与它**同一族**（族键 = ``CardBehavior.count_key``）。

    族判据只有一份：``is_slash``（= :func:`~sgs.effects.satisfies_required` 的唯一放宽）
    读的就是 ``count_key == "slash"``，故火【杀】/ 雷【杀】/ 转化【杀】在【杀】族需求下
    都满足，而这里**不另抄**一张牌名名单。
    """
    if product is None:
        return False
    if product == name:
        return True
    behavior = CARD_BEHAVIORS.get(product)
    return behavior is not None and behavior.count_key == name


def _listed(allowed: tuple[Any, ...] | None, value: Any) -> bool:
    """一个维度的三态判定：``None`` = 不限；``()`` = 什么也不满足；否则按白名单。"""
    if allowed is None:
        return True
    return value is not None and value in allowed


def _dimension_failure(demand: Demand, supply: Supply) -> str | None:
    """第一个不满足的维度，返回它的人话短语（投影拼进行的 ``reason``）；全满足 ⇒ ``None``。"""
    if demand.names is not None and not any(
        _name_allowed(name, supply.product) for name in demand.names
    ):
        return "不满足牌名要求"
    if not _listed(demand.suits, supply.suit):
        return "不满足花色限制"
    if not _listed(demand.colors, supply.color):
        return "不满足颜色限制"
    if not _listed(demand.ranks, supply.rank):
        return "不满足点数限制"
    if not _listed(demand.kinds, supply.kind):
        return "不满足虚拟 / 转化要求"
    if not _listed(demand.source_counts, supply.source_count):
        return "不满足转化源张数要求"
    return None


def supply_mismatch(demand: Demand | None, supply: Supply | None) -> str | None:
    """供给**为什么不满足**需求：``None`` = 满足（或本票不判死）。

    - 没有供给 / 没有需求：``None``（没有"不满足"可言；需求为空 = 不限）；
    - ``supply.unknown``：候选期不可知 ⇒ 本票**不判死**（返回 ``None``，由票 02/03/04
      决定它的合法性）；
    - 否则按维度顺序给出第一条不满足的人话短语。
    """
    if demand is None or supply is None:
        return None
    if supply.unknown:
        return None
    return _dimension_failure(demand, supply)


def supply_satisfies(demand: Demand | None, supply: Supply | None) -> bool:
    """这条供给满不满足这份需求（= 投影行里的 ``ok``）。

    判据与 :func:`supply_mismatch` **同源**（同一个 ``_dimension_failure``）：
    "``ok`` 为假"与"给得出理由"不可能一个有一个没有。
    """
    if supply is None:
        return False
    return supply_mismatch(demand, supply) is None


def demand_for(
    kind: QueryKind,
    payload: Any = None,
    *,
    dying: int | None = None,
    asker: int | None = None,
) -> Demand | None:
    """一次询问的 :class:`Demand`；**不是**需要牌窗口（含改判）⇒ ``None``。

    两个有生产者的维度就在这里接上（票面 §1）：

    - **牌名** = 窗口的 ``required``（【闪】窗口恒为 ``dodge``；响应窗口取自身载荷）；
    - **花色** = ``response_suits``（单骑第三段；``None`` 不限 / ``()`` 记录为空 /
      非空白名单三态原样进需求，不在这里塌成两态）。

    ``asker`` / ``dying`` 只服务求桃窗口的自救读法（见 :data:`SELF_RESCUE_CARD`）。
    窗口判据走 :func:`sgs.effects.actions.is_need_card_window`（``_window_floor`` 那一族），
    故"展示层认不认为是需要牌窗口"与引擎分面**同源**。
    """
    from .actions import is_need_card_window

    if not is_need_card_window(kind, payload):
        return None
    if kind is QueryKind.DODGE:
        ctx = payload if isinstance(payload, DodgeContext) else DodgeContext()
        return Demand(names=(DODGE_RESPONSE_CARD,), suits=ctx.response_suits)
    if kind is QueryKind.PEACH:
        names = [PEACH_RESPONSE_CARD]
        if dying is not None and asker is not None and int(asker) == int(dying):
            names.append(SELF_RESCUE_CARD)
        return Demand(names=tuple(names))
    if kind is QueryKind.RESPOND:
        if not isinstance(payload, RespondContext):
            raise ValueError("RESPOND 询问必须给出 RespondContext")
        return Demand(names=None if payload.required is None else (payload.required,))
    return None


@dataclass(frozen=True, slots=True)
class TierSupplies:
    """**一档**的供给（投影按档分组的输入）：档案 + 载体 + 逐条 :class:`Supply`。

    ``provider`` 是提供者的技能 id（【八卦阵】/【护驾】/… 那一档），逐张牌档（手牌 /
    装备区里的真牌与转化）为 ``None``——载体名由投影用既有技能名表渲染（本层不写文案）。
    """

    tier: int
    provider: str | None
    supplies: tuple[Supply, ...]


def supplies_for(
    kind: QueryKind,
    *,
    state: Any,
    seat: int,
    payload: Any = None,
    dying: int | None = None,
    answers_seq: int = 0,
) -> tuple[TierSupplies, ...]:
    """这一问的**按档供给**：从 :func:`~sgs.effects.actions.all_actions_for` 的全档候选反推。

    **不新增真源**（spec §0.1-2）：

    - 逐张供给（真牌 / 转化牌 / 纯虚拟牌）从**现有候选**反推：``UseCard`` / ``PlayCard``
      的牌面事实就是供给的牌面事实（单源转化继承产物花色 / 点数，多源转化花色点数恒无
      ——那是 ``CardRef`` 的既有事实，本层不重算）；
    - 提供者（``ActivateSkill``）的供给来自**声明**：``SkillDef.supply``（与
      ``need_card_tier`` 同一姿态——档 / 供给都是**定义**的属性，不是局面属性）；
      没声明的提供者不报供给（接入新提供者时补声明，与档位的纪律相同）。

    返回按 ``(tier, provider)`` 分组（升序）；同一档的不同载体各成一组（【八卦阵】与
    【八阵】同档），投影再把它们合成一行。
    """
    from .actions import all_actions_for, need_card_tier

    actions = all_actions_for(
        state, seat, kind, answers_seq=answers_seq, dying=dying, payload=payload
    )
    groups: dict[tuple[int, str | None], list[Supply]] = {}
    for action in actions:
        if isinstance(action, ActivateSkill):
            supply = _declared_supply(action.skill_id)
            if supply is None:
                # 没有声明的提供者不报供给（展示层不知道它能提供什么，不猜）。
                continue
            groups.setdefault((need_card_tier(action), action.skill_id), []).append(supply)
        elif isinstance(action, (UseCard, PlayCard)):
            groups.setdefault((need_card_tier(action), None), []).append(
                _supply_of_card(action.card)
            )
    return tuple(
        TierSupplies(tier=tier, provider=provider, supplies=tuple(dict.fromkeys(supplies)))
        for (tier, provider), supplies in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1] or "")
        )
    )


def _supply_of_card(card: CardRef) -> Supply:
    """一张**候选牌**的供给描述：真牌 / 单源转化 / 多源转化 / 纯虚拟。

    牌面事实**原样**取 ``CardRef``（单源转化继承产物花色 / 点数 / 颜色，多源转化三者
    恒无 —— 那是构造期的事实，本层不重算）。``kind`` 的判据是 ``virtual_sources`` /
    ``is_virtual`` 这两条既有事实：有来源 = 转化，无来源的虚拟牌 = 纯虚拟。
    """
    sources = card.virtual_sources
    if sources:
        kind, source_count = "transformed", len(sources)
    elif card.is_virtual:
        kind, source_count = "virtual", 1
    else:
        kind, source_count = "real", 1
    return Supply(
        product=card.type,
        suit=card.suit,
        rank=card.rank,
        color=card.color,
        kind=kind,
        source_count=source_count,
    )


def _declared_supply(skill_id: str) -> Supply | None:
    """提供者的**声明**供给（``SkillDef.supply``）；惰性 import（``catalog`` 反向依赖本层）。"""
    from sgs.skills import CATALOG

    definition = CATALOG.get(skill_id)
    return None if definition is None else definition.supply
