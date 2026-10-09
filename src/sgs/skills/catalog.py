"""技能**定义目录**与装备效果回调（票 07 §1–§3；施工票 29 落其静态面）。

一张装备 = 一个 :class:`SkillDef`（稳定 id = ``equip.<牌名 id>``），内部可带多条
:class:`Hook`（键 = :class:`~sgs.timing.Timing` 枚举本身）与多条
:class:`~sgs.effects.Modifier`（挂点 ∈ :class:`~sgs.effects.ModifierPoint` 的五档）。
定义目录 :data:`CATALOG` 是**模块级不可变**的；"谁持有哪一份"由 :func:`carried_skills`
在派发时按**载体**（M2 = 装备区）解析，不做 per-player 实例注册。

**修饰器管道的词汇与派发住 ``effects`` 层**（:mod:`sgs.effects.modifiers`），因为五个挂点
都由 ``effects/actions.py`` 的规则函数直接读；本模块只装**定义与回调**，方向是
``skills → effects``（`开发文档/01` §5）。

**静态 vs 触发 = 修饰器 vs 钩子**（票 07 §3，官方口径 `A/rule_forced.txt:5`：锁定技只是
强制性标签，不是时机 / 优先级）。本模块落**静态面**：次数上限 / 目标池 / 距离 / 有效性 /
候选池五挂点与诸葛连弩、仁王盾、六坐骑、方天画戟的修饰器。有时机的钩子（青釭剑 /
雌雄双股剑 / 麒麟弓 / 寒冰剑等）与帧内接缝、虚拟牌由票 30 在同一张表上补齐——故 17 条
装备定义本票**全部登记**，未落效果的条目 hooks / modifiers 暂为空元组（加载期"装备必有
技能"因此已成立）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import combinations
from types import MappingProxyType
from typing import Any, TypeAlias

from sgs.data import Color, color_of_suit
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    PriorityClass,
    priority_class_of,
)
from sgs.resolution import Control, Frame
from sgs.state import GameState, UseCard
from sgs.timing import Timing

__all__ = [
    "CATALOG",
    "Hook",
    "SkillDef",
    "carried_skills",
]


HookHandler: TypeAlias = Callable[[Any, Frame], Control]
"""钩子回调：``(host, frame) -> Control``，与步骤函数同形（票 07 §1）。"""


@dataclass(frozen=True, slots=True)
class Hook:
    """一条时机钩子：键 = :class:`~sgs.timing.Timing` 枚举本身 + 回调。

    M2 的静态票只登记空的 ``hooks``；票 30 在同一张表上填触发类装备效果。
    """

    timing: Timing
    handler: HookHandler


@dataclass(frozen=True, slots=True)
class SkillDef:
    """一张装备的完整技能定义（票 07 §2 / §6 的冻结形状）。

    ``carrier`` 决定优先级档（装备档）；``locked`` 判据逐字取官方"牌面有没有『可以』"
    （票 05 已逐条列出），M2 只登记、由票 30 的钩子派发读取。
    """

    skill_id: str
    locked: bool
    carrier: CarrierKind
    hooks: tuple[Hook, ...] = ()
    modifiers: tuple[Modifier, ...] = ()

    @property
    def priority_class(self) -> PriorityClass:
        """本技能在修饰器链里的优先级档（由载体派生，不另存字段以防漂移）。"""
        return priority_class_of(self.carrier)


# ---------------------------------------------------------------------------
# 静态装备效果的回调（票 12 §4 的落位表）
# ---------------------------------------------------------------------------


def _own_slash(ctx: ModifierContext, *, holder: int | None) -> bool:
    """``holder`` 是否**持有一张【杀】**且它就是本次查询的牌。

    【诸葛连弩】【仁王盾】【方天画戟】的入口判据形状相同（"本人 / 本牌名"），故集中一处：
    装备名与挂点的差异留给各自的条件，重复的守卫不必抄三遍。
    """
    return ctx.owner == holder and ctx.card is not None and ctx.card.type == "slash"


def _unlimited_slash(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """诸葛连弩：锁定技，你使用【杀】无次数限制（`A/flow_condition.txt:21`）。

    只对**持有者自己**使用【杀】生效；``None`` 表示"无上限"，由
    :func:`~sgs.effects.check_count` 读成"不超额"。
    """
    del state
    if not _own_slash(ctx, holder=ctx.subject):
        return current
    return None


def _mount_distance(step: int) -> Callable[[GameState, ModifierContext, Any], Any]:
    """坐骑改**座位距离**：客体系数 +1（防御马）/ 主体系数 −1（进攻马）（票 12 §Q22）。

    +1 坐骑只加"别人到你"，−1 坐骑只减"你到别人"——方向由 ``owner`` 与主体 / 客体的
    关系表达，故同一 ``DISTANCE`` 挂点能同时承载两个方向。
    """

    def handler(state: GameState, ctx: ModifierContext, current: Any) -> Any:
        del state
        affected = ctx.target if step > 0 else ctx.subject
        if affected is None or ctx.owner != affected:
            return current
        return current + step

    return handler


def _renwang_effectiveness(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """仁王盾：锁定技，**黑色的【杀】对你无效**（`A/flow_use.txt:79` / `R:1829-1830`）。

    作用点在"使用结算开始时"的有效性检测（票 15 §5 的第 2 层）：为假 ⇒ 到 C1 即终止
    对该目标的结算，**不开** C2 的响应窗口。颜色由花色派生（票 12 §Q4）。
    """
    del state
    if not current:
        return current
    if not _own_slash(ctx, holder=ctx.target):
        return current
    assert ctx.card is not None  # _own_slash 已保证
    if color_of_suit(ctx.card.suit) is Color.BLACK:
        return False
    return current


def _halberd_same_target_pool(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """方天画戟的 ``TARGET_POOL`` 挂点：**池不改**（票 12 §4 的"合法性同标准"）。

    官方一手 `A/flow_condition.txt:46` 把【方天画戟】列在"额定目标与额外目标的合法性
    检测标准**相同**"之下，故额定目标与额外目标共用同一个 :func:`~sgs.effects.check_targets`
    输出；这个挂点的意义是让那条"同一套检测"有一个明写的落点（M3 的【巧说】类
    "标准不同"效果从这里分叉），M2 本身是恒等的。
    """
    del state, ctx
    return current


def _halberd_extra_targets(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """方天画戟：此【杀】是你**最后的手牌**时可多指定至多两个目标（`A/glossary_gamecard.txt:103-105`）。

    条件判据 = 使用时刻手牌里除它之外为空（"最后两张手牌"与"最后一张手牌 + 一张装备牌"
    都**不**满足）；额外目标与原额定目标**共用**同一个目标池（``ctx.pool`` 已过
    ``TARGET_POOL``，含攻击范围检测），故"额外目标仍过同一套合法性检测"是结构性事实。
    """
    if not _own_slash(ctx, holder=ctx.subject):
        return current
    assert ctx.card is not None  # _own_slash 已保证
    if list(state.player(ctx.subject).hand) != [ctx.card.card_id]:
        return current
    base = next(
        (
            action
            for action in current
            if isinstance(action, UseCard) and action.card.card_id == ctx.card.card_id
        ),
        None,
    )
    if base is None:
        return current
    extras: list[Any] = []
    for extra in (1, 2):
        total = ctx.rated_targets + extra
        if len(ctx.pool) < total:
            continue
        extras.extend(
            base.model_copy(update={"targets": tuple(combo)})
            for combo in combinations(ctx.pool, total)
        )
    return (*current, *extras)


def _equip(
    card_type_id: str,
    *,
    locked: bool,
    modifiers: tuple[Modifier, ...] = (),
    hooks: tuple[Hook, ...] = (),
) -> SkillDef:
    """按牌名 id 造一条装备技能定义（``skill_id = "equip." + 牌名 id``，票 09 §1）。"""
    return SkillDef(
        skill_id=f"equip.{card_type_id}",
        locked=locked,
        carrier=CarrierKind.EQUIPMENT,
        hooks=hooks,
        modifiers=modifiers,
    )


_DISTANCE_PLUS_ONE = (Modifier(ModifierPoint.DISTANCE, _mount_distance(1)),)
_DISTANCE_MINUS_ONE = (Modifier(ModifierPoint.DISTANCE, _mount_distance(-1)),)

_SKILL_DEFS: tuple[SkillDef, ...] = (
    # 9 名武器 / 10 实体张（诸葛连弩 ×2）
    _equip(
        "crossbow",
        locked=True,
        modifiers=(Modifier(ModifierPoint.COUNT_LIMIT, _unlimited_slash),),
    ),
    _equip("qinggang_sword", locked=True),
    _equip("double_sword", locked=False),
    _equip("green_dragon_blade", locked=False),
    _equip("serpent_spear", locked=False),
    _equip("axe", locked=False),
    _equip(
        "halberd",
        locked=False,
        modifiers=(
            Modifier(ModifierPoint.TARGET_POOL, _halberd_same_target_pool),
            Modifier(ModifierPoint.CANDIDATES, _halberd_extra_targets),
        ),
    ),
    _equip("qilin_bow", locked=False),
    _equip("ice_sword", locked=False),
    # 3 防具
    _equip("eight_diagrams", locked=False),
    _equip(
        "renwang_shield",
        locked=True,
        modifiers=(Modifier(ModifierPoint.EFFECTIVENESS, _renwang_effectiveness),),
    ),
    # 6 坐骑：−1 进攻马（赤兔 ♥5）/ +1 防御马（绝影 ♠5）/ +1 防御马（的卢 ♣5）/
    # +1 防御马（爪黄飞电 ♥K）/ −1 进攻马（大宛 ♠K）/ −1 进攻马（紫骍 ♦K）
    _equip("chitu", locked=True, modifiers=_DISTANCE_MINUS_ONE),
    _equip("jueying", locked=True, modifiers=_DISTANCE_PLUS_ONE),
    _equip("dilu", locked=True, modifiers=_DISTANCE_PLUS_ONE),
    _equip("zhuahuang_feidian", locked=True, modifiers=_DISTANCE_PLUS_ONE),
    _equip("dawan", locked=True, modifiers=_DISTANCE_MINUS_ONE),
    _equip("zixing", locked=True, modifiers=_DISTANCE_MINUS_ONE),
)

CATALOG: Mapping[str, SkillDef] = MappingProxyType(
    {definition.skill_id: definition for definition in _SKILL_DEFS}
)
"""定义目录：``skill_id`` → :class:`SkillDef`，**模块级不可变**（票 07 §1 / §6）。

没有可变全局注册表：M2 的 17 条是代码侧字面量，M3 换数据来源时字段名不变
（``register`` 式写入随 M3 的数据驱动加载一起加，M2 不需要）。
"""


def carried_skills(state: GameState) -> tuple[tuple[int, SkillDef], ...]:
    """解析"谁持有哪一份技能"：遍历**存活**座位的装备区，按载体查出定义。

    返回 ``(owner, SkillDef)`` 元组，按 **``(priority_class, skill_id)``** 全序排好
    （票 07 §6 的冻结链序）；同一 ``skill_id`` 出现在多个座位（两张同名装备 / 多人同坐骑）
    时，稳定排序保留**确定性的载体遍历序**（座位升序 → 槽位枚举序），故链序仍是全序、
    同种子逐字节可复现。

    死者的装备在真实流程里已随死亡收尾清空，这里跳过死者只是把不变量写死。
    """
    entries: list[tuple[PriorityClass, str, int, SkillDef]] = []
    for player in state.players:
        if not player.alive:
            continue
        for card_id in player.equip:
            card = state.card(card_id)
            definition = CATALOG.get(f"equip.{card.type}")
            if definition is None:
                continue
            entries.append(
                (definition.priority_class, definition.skill_id, player.seat, definition)
            )
    entries.sort(key=lambda entry: (int(entry[0]), entry[1]))
    return tuple((owner, definition) for _priority, _skill_id, owner, definition in entries)
