"""虚拟牌 / 转化牌的构造与 id 派生（票 12 §6 / 票 30；票 03 泛化）。

**判据是 ``card_id < 0``**（票 12 §Q21）：虚拟牌不参与牌守恒与区域互斥——它从不进入
任何区域（``GameState.card`` 对负 id fail-closed），因此"牌守恒"这条整局不变量对它
天然成立。虚拟牌的 ``suit`` / ``rank`` / ``color`` 都可空。

**三形态的牌面事实**（ADR-003 / ADR-004；`03` §5「效果原语（Effect Primitives）清单」的四列表）：

- **单源转化**（武圣 / 龙胆 / 国色 / 急救）：继承原牌的**花色 / 点数 / 颜色**；
- **多源转化**（【丈八蛇矛】两张手牌当【杀】）：花色**恒无**、点数**恒无**、颜色按
  "多张牌同色则有该颜色、否则无色"派生（ADR-004 逐字）；
- **纯虚拟牌**（【八卦阵】视为使用/打出的【闪】）：三者皆无
  （`A/glossary_gamecard.txt:127`）。

**属性一律不继承**（ADR-003）：属性**由效果决定**，效果未声明即无属性（= 普通伤害）。
这条在本项目是**结构事实**——:class:`~sgs.state.CardRef` 根本没有属性字段，故
"武圣转化的【杀】是普通【杀】"不可能被某个构造器悄悄改掉。

id 由**来源实体牌 id** 确定性地派生（同种子逐字节可复现）：同一目标牌名 + 同一组来源
恒得同一个 id、**不同来源不同 id**（来源集合的枚举序不影响，构造时先排序）。票 03 把
命名空间从"最多两张来源"泛化到 :data:`MAX_VIRTUAL_SOURCES` 张：每个**目标牌名**一段
:class:`VirtualKind` 命名空间，纯虚拟牌与一切转化牌（含单源、多源）逐对不撞 id。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import IntEnum
from itertools import combinations

from sgs.data import Color
from sgs.effects import (
    CandidateScope,
    ModifierContext,
    ModifierPoint,
    TargetCount,
    apply_modifiers,
    behavior_for,
    check_count,
    check_targets,
    forbidden_as_slash,
    forced_use_targets,
    is_slash,
    satisfies_required,
    target_combos,
)
from sgs.state import (
    CardRef,
    GameState,
    PlayCard,
    RespondMode,
    UseCard,
    Zone,
)

__all__ = [
    "MAX_VIRTUAL_SOURCES",
    "WIRE_INT_LIMIT",
    "SourceFilter",
    "VirtualKind",
    "VirtualSourceError",
    "card_ids",
    "grouped_candidates",
    "kind_source_ceiling",
    "pure_virtual_card",
    "single_conversion",
    "transformed_card",
    "transformed_slash",
    "virtual_card_id",
    "virtual_card_use",
    "virtual_candidates",
    "virtual_dodge",
]


class VirtualKind(IntEnum):
    """虚拟牌的**目标牌名命名空间**（票 03 §要做什么 1「每个目标牌名一段 kind」）。

    显式常量而不是按牌名取哈希：值必须**跨进程 / 跨运行稳定**（确定性纪律），
    且既有值（``0`` = 转化【杀】、``1`` = 视为【闪】）**一字不改**。新增牌名时
    在此追加一行，旧 id 不漂移。
    """

    SLASH = 0
    DODGE = 1
    PEACH = 2
    INDULGENCE = 3
    DUEL = 4
    FIRE_SLASH = 5
    DISMANTLEMENT = 6
    WINE = 7
    """【酒】（M6 票 16 的龙胆「桃当酒」）：**尾部紧凑段**的第一个 kind。

    追加段挂在 ``kind * block`` 之后，而 108 张档的 ``kind = 7`` 段起点已经
    ``> WIRE_INT_LIMIT``（7 * 1.46e15 ≈ 1.02e16 > 2**53）。故从这里起的 kind 改用
    :func:`_tail_segment_start` 的紧凑段：段容量只需（来源张数 + 1）× 进制的平方，
    且整段落在**兼容段与泛化段之间的未用空隙**里——旧 id 一字不动。
    """

    # 移动版于吉【蛊惑】（用户 2026-11-01）是**第一条"任意牌名"**的转化技：它的产物覆盖
    # 全部基本牌 + 普通锦囊牌，故这些牌名全部需要一段命名空间。追加段只增不改：旧 id
    # （0–7 段）一字不动，尾部紧凑段按 :func:`_tail_segment_start` 依次排开。
    THUNDER_SLASH = 8
    SNATCH = 9
    BARBARIAN_INVASION = 10
    ARROW_BARRAGE = 11
    PEACH_GARDEN = 12
    HARVEST = 13
    EX_NIHILO = 14
    BORROWED_SWORD = 15
    NULLIFY = 16
    FIRE_ATTACK = 17
    IRON_CHAIN = 18


BLOCK_KIND_COUNT = 6
"""**共享冻结段布局**的 kind 数（``SLASH`` … ``FIRE_SLASH``，即 ``kind < 6``）。

``MAX_VIRTUAL_SOURCES = 7`` 是按这六段一起塞进 :data:`WIRE_INT_LIMIT` 反推的紧界
（``6 * 8 * 109**7 ≈ 8.77e15 < 2**53``）；每段的**块容量** ``(7+1) * base**7`` 因此
按六段冻结。M6 票 03 起**追加**的 kind（``kind >= 6``）挂在旧段之后（``1 + kind *
block``），段起点已逼近线上整数上限，故每一段另有自己的来源张数上界
（:func:`kind_source_ceiling`）——**块容量与旧 id 一字不改**。
"""


LEGACY_KIND_COUNT = 2
"""**兼容命名空间**的段数（票 03 的确定性契约，不是可调参数）。

``0`` / ``1`` 两段的 id **必须与旧算式逐字节相同**：M2 的 Golden Replay 磁带里记着
【丈八蛇矛】转化【杀】的 ``card_id``（如 ``-11882``），而门禁要求 34 条基准一行不改
（票 14 §7）。故这两段保留旧算式，票 03 的泛化从 ``2`` 起另起一段**更大的**区间——
两段区间互不重叠（见 :func:`_generalized_block` 的界推）。
"""


_KIND_BY_TYPE: dict[str, VirtualKind] = {
    "slash": VirtualKind.SLASH,
    "dodge": VirtualKind.DODGE,
    "peach": VirtualKind.PEACH,
    "indulgence": VirtualKind.INDULGENCE,
    "duel": VirtualKind.DUEL,
    "fire_slash": VirtualKind.FIRE_SLASH,
    "dismantlement": VirtualKind.DISMANTLEMENT,
    "wine": VirtualKind.WINE,
    # 移动版于吉【蛊惑】的产物牌名（基本牌 + 普通锦囊，用户 2026-11-01）：
    "thunder_slash": VirtualKind.THUNDER_SLASH,
    "snatch": VirtualKind.SNATCH,
    "barbarian_invasion": VirtualKind.BARBARIAN_INVASION,
    "arrow_barrage": VirtualKind.ARROW_BARRAGE,
    "peach_garden": VirtualKind.PEACH_GARDEN,
    "harvest": VirtualKind.HARVEST,
    "ex_nihilo": VirtualKind.EX_NIHILO,
    "borrowed_sword": VirtualKind.BORROWED_SWORD,
    "nullify": VirtualKind.NULLIFY,
    "fire_attack": VirtualKind.FIRE_ATTACK,
    "iron_chain": VirtualKind.IRON_CHAIN,
}
"""目标**牌名 id** → 命名空间；只登记本里程碑有生产者的牌（票 03 §表 / 票 04 加【决斗】；
M5-05b 加朱雀羽扇的火【杀】产物；M6 票 03 加【奇袭】的【过河拆桥】；M6 票 16 加界【龙胆】
的【桃】当【酒】——它走尾部紧凑段，因为追加段的段起点已经越过线上上限）。雷杀当前没有
生产者，故**不登记**——空段会白占 ``KIND_COUNT``，把 108 的来源上限从 7 压到 6 并移动
旧泛化段。"""

KIND_COUNT = len(VirtualKind)
"""命名空间的段数（只读事实；id 算式不读它，构造期的上界检查读它作为段数）。"""

WIRE_INT_LIMIT = 2**53
"""虚拟 id 的**线上整数上限**：JS ``Number`` 只把 ``|id| < WIRE_INT_LIMIT`` 的整数读成精确整数。

线上 id 是 JSON 数字，浏览器 ``JSON.parse`` 只精确到 ``Number.MAX_SAFE_INTEGER``
（``2**53 - 1``）；超过即变浮点、回传被 pydantic 拒绝（手动验收实测 ``-7.006...e+183``）。
这是**唯一**的真源：构造期的进制检查 :func:`_source_ceiling_for_base` 与
``tests/test_virtual_ids.py`` 都从它取，不再各写一份 ``2**53`` 字面量（W4-fix-5）。
"""

MAX_VIRTUAL_SOURCES = 7
"""虚拟牌的**来源张数上界**（108 张基准包；票 03 §验收 4；W4-fix-1 由 :data:`WIRE_INT_LIMIT` 反推）。

:func:`_generalized_block` 的一段容量是 ``(MAX+1) * base**MAX``（紧界），而共享冻结段
（:data:`BLOCK_KIND_COUNT` 个 kind）的全体 id 上界是 ``BLOCK_KIND_COUNT * block``；
标准包 ``base = 109``、共享段 = 6 个 kind（M5-05b 追加火【杀】段后）时

- ``MAX = 7``：``6 * 8 * 109**7 ≈ 8.77e15 < 2**53 ≈ 9.01e15`` ✓
- ``MAX = 8``：``6 * 9 * 109**8 ≈ 1.07e18`` ✗

故 7 是 108 张牌堆下编码撑得住的最大值。``tests/test_virtual_ids.py`` 从
:data:`WIRE_INT_LIMIT` 重新推导它，不让这个数字成为魔法数。

**更大的牌堆**（M5 的 160 张军争，``base = 161``）撑不住 7 张来源的编码；构造期由
:func:`_source_ceiling_for_base` 按同一条界**收紧**到 6 张（``6 * 7 * 161**6 ≈ 7.31e14``）。
上界是 ``min(7, 撑得住的最大 m)``：更小的牌堆（53 张 ⇒ ``base = 54``）数学上容得下
8 张，也**不**改大——兼容段与既有金录像按 7 冻结。这是按线上整数上限收紧，不是放宽：
160 张的实际上界 6 仍远大于本里程碑的最多两张来源（【丈八蛇矛】）。超过上界的来源集合
由 :func:`transformed_card` / :func:`virtual_card_id` 抛 :class:`VirtualSourceError`
（构造期可定位错误），而不是静默产出撞 id 或会被浏览器变形的虚拟牌。

**追加 kind 的来源上界**（M6 票 03 起）：``kind >= BLOCK_KIND_COUNT`` 的段挂在旧段之后，
段起点 ``1 + kind * block`` 已逼近线上整数上限，故每段按
:func:`kind_source_ceiling` 单独收窄（108 张档的 ``DISMANTLEMENT`` 段 = 6 张，而不是 7）。
``MAX_VIRTUAL_SOURCES`` 仍是**共享冻结段**的上界，也是各段上界的取值上限。
"""


class VirtualSourceError(ValueError):
    """来源集合不合法（空 / 超过本副牌堆的来源张数上界）。

    继承 ``ValueError``：这是构造期的编程错误，与数据加载期的聚合报错同姿态——
    早抛、能定位，而不是让一张撞 id 的虚拟牌流进对局。
    """


def _base(card_count: int) -> int:
    """进制 = ``card_count + 1``：来源 id 全部落进 ``0..card_count-1``，故恒小于它。"""
    return card_count + 1


def _source_ceiling_for_base(base: int) -> int:
    """共享冻结段**撑得住**的来源张数上界：``min(MAX_VIRTUAL_SOURCES, 撑得住的最大 m)``。

    109 进制下 7 张是紧上界；牌堆变大（M5 的 160 张 ⇒ ``base = 161``）时
    ``base**7`` 让 7 张来源的编码越过 :data:`WIRE_INT_LIMIT`，故按同一条界取满足
    ``BLOCK_KIND_COUNT * (m+1) * base**m < WIRE_INT_LIMIT`` 的最大 ``m``。**上限恒为
    :data:`MAX_VIRTUAL_SOURCES`**：更小的牌堆（53 张 ⇒ ``base = 54``）数学上能容纳
    8 张，但兼容段 / 既有金录像按 7 冻结，不因牌堆变小而改大。

    段数读 :data:`BLOCK_KIND_COUNT`（共享冻结段的 6 个 kind），**不是**
    ``len(VirtualKind)``：M6 票 03 追加的 kind 不参与旧块布局的界推，它另有自己的
    :func:`kind_source_ceiling`。若这里读成 7 个 kind，108 张档会收紧到 6 张并挪动
    旧泛化段的全部 id（国色 / 急救等既有的虚拟牌字节就变了）。

    下限是 **2**：兼容段的 ``_fold_legacy`` 用 ``base**2`` 作段步长，两来源的兼容 id
    必须仍在线内。``m = 2`` 都撑不住时 fail-closed，抛 :class:`VirtualSourceError`
    ——不静默产 id。
    """
    for sources in range(MAX_VIRTUAL_SOURCES, 1, -1):
        if BLOCK_KIND_COUNT * (sources + 1) * base**sources < WIRE_INT_LIMIT:
            return sources
    raise VirtualSourceError(
        f"virtual_card_id: 当前进制撑不住任何来源张数——base={base}"
        f"（牌堆 {base - 1} 张）时连两张来源的编码都越过线上整数上限 {WIRE_INT_LIMIT}"
    )


def kind_source_ceiling(kind: int, card_count: int) -> int:
    """某一段（``kind``）在给定牌堆下的**来源张数上界**（线格式硬约束）。

    共享冻结段（``kind < BLOCK_KIND_COUNT``）直接读 :func:`_source_ceiling_for_base`
    的牌堆上界；追加段（``kind >= BLOCK_KIND_COUNT``）的段起点是 ``1 + kind * block``，
    剩余线内空间更小，故按同一不等式从牌堆上界往下收窄：

    ``kind * block + (L + 1) * base**L < WIRE_INT_LIMIT``

    （``L`` = 来源张数；``(L + 1) * base**L`` 是该段 ``L`` 张来源的可能编码上界。
    ``block`` 仍按**共享段**的紧界取——追加段不移动旧 id，也不改块容量。）

    108 张档（``base = 109``、``block ≈ 1.46e15``）下 ``DISMANTLEMENT``（kind = 6）的
    段起点 ``≈ 8.77e15``，只剩约 ``2.3e14`` 的线内空间：``L = 7`` 的 7 张来源会越过
    ``2**53``，``L = 6`` 仍在界内 ⇒ 上界 6。160 张档同样 6（与共享段相同）。

    **尾部紧凑段**（M6 票 16 起，``kind >= TAIL_KIND_START``）：追加段的段起点
    ``kind * block`` 已经超出线上上限（108 张档的 ``kind = 7`` ⇒ ``≈ 1.02e16 > 2**53``），
    故这类 kind 改用落在兼容段与泛化段之间空隙里的紧凑段（:func:`_tail_source_ceiling`），
    上界按同一不等式在该空隙里反推（108 / 160 档都是 2）。
    """
    if kind >= TAIL_KIND_START:
        base = _base(card_count)
        _tail_segment_start(base, kind)  # 段不相交的构造期校验（越界即抛）
        return _tail_source_ceiling(base)
    base = _base(card_count)
    shared = _source_ceiling_for_base(base)
    if kind < BLOCK_KIND_COUNT:
        return shared
    block = _generalized_block(base=base, source_ceiling=shared)
    start = kind * block
    for sources in range(shared, 1, -1):
        if start + (sources + 1) * base**sources < WIRE_INT_LIMIT:
            return sources
    raise VirtualSourceError(
        f"virtual_card_id: kind={kind}（牌堆 {card_count} 张）的段起点 {start} 之后"
        f"连两张来源的编码都越过线上整数上限 {WIRE_INT_LIMIT}"
    )


def virtual_card_id(
    *,
    kind: int,
    sources: tuple[int, ...],
    card_count: int,
) -> int:
    """由 ``(kind, 来源 id)`` 派生一个**负数** id，不同组合不重复（确定性）。

    两种命名空间按 ``kind`` 分段（票 03 §要做什么 1；判据见 :data:`LEGACY_KIND_COUNT`）：

    - ``kind < 2``（【丈八蛇矛】的转化【杀】/【八卦阵】的视为【闪】）走**兼容编码**：
      逐张来源按 ``base = card_count + 1`` 进制折叠，段步长恒为 ``base ** 2``。这三个
      数字与旧算式**逐字节相同**，故 M2 的 Golden Replay 磁带一行不改；
    - ``kind >= 2``（票 03 起新增的目标牌名）走**泛化编码**：张数哨兵 + 每张来源一位
      ``base`` 进制的紧凑折叠（:func:`_fold`），每段容量取紧界
      :func:`_generalized_block`。全体 id 因此严格小于
      ``KIND_COUNT * (MAX_VIRTUAL_SOURCES + 1) * base ** MAX_VIRTUAL_SOURCES``，
      而来源上限 :data:`MAX_VIRTUAL_SOURCES` 正是按它 ``< WIRE_INT_LIMIT`` 反推的。
    - ``kind >= TAIL_KIND_START``（M6 票 16 起）走**尾部紧凑段**：追加段的段起点已经
      越过线上上限，故这些 kind 落在兼容段与泛化段之间的空隙里
      （:func:`_tail_segment_start`），整段不与任何旧 id 相交。

    两段区间互不重叠：泛化段的起点 ``1 + 2 * block`` 大于兼容段的上界
    ``1 + base ** 2``（``block = (ceiling+1) * base**ceiling > base**2``，``ceiling >= 2``）。

    ``base`` 撑不住 :data:`MAX_VIRTUAL_SOURCES` 时按 :func:`_source_ceiling_for_base`
    收紧到本副牌堆撑得住的来源张数（108 张 ⇒ 7；160 张 ⇒ 6）；连两张（兼容段
    ``base**2`` 的下限）都撑不住时抛 :class:`VirtualSourceError`——"线上收得下"由
    **构造期**兜住，而不是交给牌堆大小碰运气（W4-fix-5）。

    签名保持既有的 ``kind`` / ``sources`` / ``card_count`` 三个关键字——既有调用点与测试
    不破；``card_count`` 仍作进制用（"来源张数上限"那层由每副牌堆的实际上界显式承担）。
    """
    base = _base(card_count)
    ceiling = kind_source_ceiling(kind, card_count)
    # 空来源是**合法的**（纯虚拟牌走这一段）；"转化牌至少一张来源"由
    # :func:`transformed_card` 把关，id 派生本身不区分二者。
    _validate_source_count(sources, max_sources=ceiling, target="virtual_card_id")
    if kind < LEGACY_KIND_COUNT:
        mixed = _fold_legacy(base=base, sources=sources)
        return -(1 + kind * base * base + mixed)
    mixed = _fold(base=base, sources=sources)
    if kind >= TAIL_KIND_START:
        return -(1 + _tail_segment_start(base, kind) + mixed)
    block = _generalized_block(
        base=base, source_ceiling=_source_ceiling_for_base(base)
    )
    return -(1 + kind * block + mixed)


def _generalized_block(*, base: int, source_ceiling: int | None = None) -> int:
    """泛化命名空间的一段容量：``(m + 1) * base**m``（``m`` = 本副牌堆的来源张数上界）。

    ``m = MAX_VIRTUAL_SOURCES``（109 进制）时就是 :func:`_fold` 在 ``L`` 张来源时落在
    ``[L * base**L, (L+1) * base**L - 1]`` 所对应的紧界，故 ``L <= m`` 的全部编码严格
    小于本容量。这个界是**紧的**（只差 1），不是旧版按 ``10`` 个十进制位撑出来的天文数
    ——正因如此 ``MAX_VIRTUAL_SOURCES`` 才能压进 :data:`WIRE_INT_LIMIT`。各 ``kind`` 段因此
    首尾不相接：第 ``k`` 段的 ``|id|`` 落在 ``[1 + k * block, (k+1) * block]``，下一段从
    ``1 + (k+1) * block`` 起算。``source_ceiling`` 缺省按 ``base`` 现取（160 张的牌堆
    会收到 6），调用方已有值时显式传入，避免在热路径重复推导。
    """
    ceiling = _source_ceiling_for_base(base) if source_ceiling is None else source_ceiling
    return (ceiling + 1) * base**ceiling


TAIL_KIND_START = int(VirtualKind.WINE)
"""**尾部紧凑段**的起点 kind（M6 票 16 的龙胆「桃当酒」是第一个消费者）。"""

TAIL_SOURCE_CEILING = 2
"""尾部紧凑段每段的来源张数上界（实际松紧由 :func:`_tail_source_ceiling` 按进制重算）。

酒 / 桃 都是**单源**转化，2 已留出余量；容量按 ``(m + 1) * base**m`` 取紧界，故整段只占
``3 * base**2`` 个数——放进兼容段与泛化段之间的空隙里，几十亿个追加 kind 也不会撞到
``2 * block`` 的泛化段起点。
"""


def _tail_offset(base: int) -> int:
    """尾部紧凑段的第一个字节偏移：紧接**兼容段**（``kind 0..1``）之后。

    兼容段的 id 落在 ``[1, 1 + LEGACY_KIND_COUNT * base**2)``；泛化段从
    ``1 + LEGACY_KIND_COUNT * block`` 起算。两者之间是一条从未被使用的空隙（兼容段的段
    步长只有 ``base**2``），尾部段就住这里——旧 id 因此一字不动。
    """
    return LEGACY_KIND_COUNT * base * base


def _tail_source_ceiling(base: int) -> int:
    """尾部紧凑段在当前进制下的来源张数上界（按线上整数上限反推，与共享段同一不等式）。"""
    offset = _tail_offset(base)
    for sources in range(TAIL_SOURCE_CEILING, 0, -1):
        if offset + (sources + 1) * base**sources < WIRE_INT_LIMIT:
            return sources
    raise VirtualSourceError(
        f"virtual_card_id: 尾部紧凑段在 base={base} 下连一张来源都撑不住"
        f"（越过线上整数上限 {WIRE_INT_LIMIT}）"
    )


def _tail_block(base: int) -> int:
    """尾部紧凑段一段的容量：``(m + 1) * base**m``（``m`` = 该进制的尾部来源上界）。"""
    ceiling = _tail_source_ceiling(base)
    return (ceiling + 1) * base**ceiling


def _tail_segment_start(base: int, kind: int) -> int:
    """尾部 kind 的段起点（``_fold`` 混合值之前的绝对偏移）。

    整段必须仍落在兼容段与泛化段之间的空隙里：``start + block <= 2 * block``（泛化段从
    ``1 + 2 * block`` 起算）。越界说明用户代码登记了一个荒唐的 ``VirtualKind`` 值，在
    构造期抛——不静默产出撞 id 的牌。
    """
    block = _tail_block(base)
    start = _tail_offset(base) + (kind - TAIL_KIND_START) * block
    generalized = LEGACY_KIND_COUNT * _generalized_block(base=base)
    if start + block > generalized:
        raise VirtualSourceError(
            f"virtual_card_id: kind={kind} 的尾部段 [{start}, {start + block}) 越出"
            f"兼容段与泛化段之间的空隙（上限 {generalized}）"
        )
    return start


def _fold(*, base: int, sources: tuple[int, ...]) -> int:
    """泛化编码：最高一段是张数哨兵，其后逐张来源各占一位 ``base`` 进制。

    展开式是 ``L * base**L + sum_i s_i * base**(L-1-i)``（``L = len(sources)``，
    每张来源 ``s_i < base``）。**单射证明**（不是注释保证，判据见
    ``tests/test_virtual_ids.py``）：固定 ``L`` 时编码落在
    ``[L * base**L, (L+1) * base**L - 1]``；相邻两段严格递增且不相交，因为
    ``(L+1) * base**L - 1 < (L+1) * base**(L+1)``（``base >= 2``）。故 ``L`` 可由
    编码唯一读出；固定 ``L`` 后余下的定长 ``base`` 进制展开也唯一——这正是旧算式只
    在最多两张来源时成立、第 ``3`` 张起就撞 id 的原因，泛化编码把它补上。
    """
    mixed = len(sources)
    for card_id in sources:
        mixed = mixed * base + card_id
    return mixed


def _fold_legacy(*, base: int, sources: tuple[int, ...]) -> int:
    """**兼容编码**（旧算式逐字保留，票 03 的确定性契约）：只用 ``base`` 作进制。

    它没有张数哨兵，故**只对最多两张来源**单射——但兼容段里的合法来源集合恰好只有
    "零张 / 一张 / 两张"（纯虚拟牌与 :func:`transformed_slash`），故在这段里它仍是单射。
    新增来源张数的泛化一律走 :func:`_fold`，不去动这三个数字。
    """
    mixed = 0
    for card_id in sources:
        mixed = mixed * base + card_id
    return mixed



def _validate_source_count(
    sources: Sequence[int], *, max_sources: int, target: str
) -> None:
    """来源张数的上界校验（票 03 §验收 4）：超过即抛可定位的错误。"""
    if len(sources) > max_sources:
        raise VirtualSourceError(
            f"{target}: 来源 {len(sources)} 张超过上界 {max_sources}"
            f"（本副牌堆的上界由 _source_ceiling_for_base 收紧）"
        )


def _require_sources(sources: Sequence[int], *, target: str) -> None:
    """转化牌的"至少一张来源"校验：空集合是构造期错误，不是"纯虚拟牌"。"""
    if not sources:
        raise VirtualSourceError(f"{target}: 转化牌至少要有一张来源牌（sources 为空）")


def _common_color(state: GameState, sources: tuple[int, ...]) -> Color | None:
    """多张转化："这些牌的颜色均相同 ⇒ 有该颜色，否则无色"（`A/glossary_gamecard.txt:91`）。

    颜色由每张来源牌的 ``CardRef.color``（花色派生的唯一真源）读，不重抄花色映射。
    """
    colors = [state.card(card_id).color for card_id in sources]
    first = colors[0]
    return first if all(color is first for color in colors) else None


def transformed_card(
    state: GameState, sources: tuple[int, ...], target_type_id: str
) -> CardRef:
    """**共享构造器**：把 ``sources`` 这几张实体牌转化成 ``target_type_id`` 牌名的牌。

    三形态在这里由**来源张数**分派（票 03 §要做什么 2）：

    - 单源 ⇒ 继承原牌**花色 / 点数 / 颜色**；
    - 多源 ⇒ 花色恒无、点数恒无、颜色按同色派生；
    - 属性一律不继承（ADR-003：属性由效果决定，未声明即普通伤害）；
    - ``virtual_sources`` 记来源实体牌 id（**排序后**，故枚举序不影响 id）；
    - 负 id 判据不变（:attr:`CardRef.is_virtual`）。

    ``sources`` 为空或超过**该牌名所在段**的来源上界（108 张档的共享段 ⇒ 7、
    ``DISMANTLEMENT`` 段 ⇒ 6；160 张档 ⇒ 6，见 :func:`kind_source_ceiling`）时抛
    :class:`VirtualSourceError`——"不产生撞 id 的虚拟牌"这条靠**构造期**兜住，
    而不是靠调用方自律。
    """
    _require_sources(sources, target=transformed_card.__name__)
    kind = _kind_of(target_type_id)
    ceiling = kind_source_ceiling(int(kind), len(state.cards))
    _validate_source_count(sources, max_sources=ceiling, target=transformed_card.__name__)
    ids = tuple(sorted(sources))
    first = state.card(ids[0])
    single = len(ids) == 1
    return CardRef(
        card_id=virtual_card_id(kind=kind, sources=ids, card_count=len(state.cards)),
        type=target_type_id,
        name=_card_name(state, target_type_id),
        suit=first.suit if single else None,
        rank=first.rank if single else None,
        color=first.color if single else _common_color(state, ids),
        virtual_sources=ids,
    )


def _kind_of(target_type_id: str) -> VirtualKind:
    """目标牌名 id → 命名空间；未登记的牌名当场抛错（不静默挤进别的段）。"""
    try:
        return _KIND_BY_TYPE[target_type_id]
    except KeyError:  # pragma: no cover - 由新增牌名时补表覆盖
        raise VirtualSourceError(
            f"虚拟牌的牌名 {target_type_id!r} 还没有命名空间：请先登记 VirtualKind"
        ) from None


def _card_name(state: GameState, target_type_id: str) -> str:
    """目标牌名的**显示名**：读实体牌里该牌名的第一张（牌面事实的唯一真源）。

    虚拟牌不是实体牌，但牌名文本必须与实体牌一致；从既有 ``CardRef`` 里读而不是
    回头 import 内容包：构造器只依赖 ``state``，与 :func:`_common_color` 同姿态。
    """
    for card in state.cards:
        if card.type == target_type_id:
            return card.name
    return target_type_id  # pragma: no cover - 标准包里每个牌名都有实体牌


def transformed_slash(state: GameState, sources: tuple[int, ...]) -> CardRef:
    """【丈八蛇矛】把两张手牌**转化**成一张【杀】（票 12 §Q21 / U-12-2/U-12-3）。

    共享构造器的**薄包装**（票 03：既有调用点与测试不破）。与一手原文的两处偏离按
    用户裁定登记（实现期登记、不改冻结文档）：

    - **点数恒无**（U-12-3）：一手 `A/glossary_gamecard.txt:95` 写"点数为各牌点数之和"，
      用户裁定**不采用**（ADR-004 的字面在本图成立）；
    - **有距离限制**（U-12-2）：与 `:125` 的"无距离限制"相反；M2 不可观测（标准版没有
      "视为使用【杀】"的生产者，本函数是**转化**）。
    """
    return transformed_card(state, sources, "slash")


def pure_virtual_card(state: GameState, target_type_id: str) -> CardRef:
    """**纯虚拟牌**：没有来源实体牌，牌面（花色 / 点数 / 颜色）三者皆无（ADR-004）。

    与 :func:`transformed_card` 的分工是构造期的硬边界（票 03 §验收 4）：转化牌**必须**
    至少一张来源，纯虚拟牌**必须**没有来源。生产者：【八卦阵】的视为【闪】、
    【离间】的视为使用【决斗】（票 04）。id 走 :func:`virtual_card_id` 的**空来源**段，
    故同一牌名的纯虚拟牌恒得同一个 id、且与一切转化牌不撞。
    """
    return CardRef(
        card_id=virtual_card_id(
            kind=_kind_of(target_type_id), sources=(), card_count=len(state.cards)
        ),
        type=target_type_id,
        name=_card_name(state, target_type_id),
        suit=None,
        rank=None,
        color=None,
    )


def virtual_dodge(state: GameState) -> CardRef:
    """【八卦阵】判定为红时"**视为**使用/打出【闪】"的纯虚拟牌（票 10 §5.3）。

    **纯虚拟牌**：没有来源实体牌（``virtual_sources`` 为空），牌面三者皆无。它是"这次
    需要"的产物、与座位无关，故 id 里不含持有者——同一次需要里只用它一次即可。
    """
    return pure_virtual_card(state, "dodge")


# ---------------------------------------------------------------------------
# 转化的候选接入（票 03 §要做什么 4 / §机制面的统一约定「候选面」）
# ---------------------------------------------------------------------------


def card_ids(state: GameState, seat: int, zone: Zone) -> tuple[int, ...]:
    """``seat`` 在 ``zone`` 里的牌 id（手牌 / 装备区 / 判定区按区序）。

    只读现状、不改状态：来源筛选是**候选生成期**的事，真正被转化消耗的实体牌由
    ``virtual_sources`` 在结算段走正常移动路径（票 12 §Q21）。
    """
    player = state.player(seat)
    if zone is Zone.HAND:
        return tuple(player.hand)
    if zone is Zone.EQUIP:
        return tuple(player.equip)
    if zone is Zone.JUDGE:
        return tuple(player.judge)
    return ()


def virtual_card_use(
    state: GameState,
    seat: int,
    card: CardRef,
    ctx: Any,
) -> list[Any]:
    """一张转化牌在本次 ``CANDIDATES`` 派发里的全部行动（**使用 / 打出共用一份**）。

    与真牌**同一条**合法性检测（票 03 §要做什么 4：「目标池走与真牌同一套
    ``check_targets``（【丈八蛇矛】的先例）」）：

    - ``PLAY``：按目标池枚举 ``额定..最大目标数`` 的组合——最大数只在
      ``TargetCount.RATED`` 档读 :data:`~sgs.skills.ModifierPoint.TARGET_COUNT` 链
      （W7-02：方天画戟在"最后一张手牌"的转化【杀】上把 1 放宽到 3，与出牌阶段真牌的
      :func:`~sgs.effects.actions._play_phase` 读**同一份**链值 / 同一个组合构建子），
      并先过 ``COUNT_LIMIT``（按**目标牌名**计数，与真牌同一条链——票 03 §要做什么 4 /
      规则 16.4「按目标牌名统计使用次数」）；
    - ``RESPOND`` + ``mode = PLAY``：一个 ``PlayCard``（打出不占次数）；
    - ``RESPOND`` + ``mode = USE``：窗口固定了目标（借刀的 Bn）时先问
      :func:`forced_use_targets`"够不够得着 Bn"（W5-02b 的 a 可行性 / auto-b 语义），
      再拿**完整** :func:`check_targets` 池 + :func:`target_combos` 造
      ``(Bn,) + 0..2`` 个其它目标的组合（W7-02：与 ``_respond`` 的 USE 趟同形，
      ``TARGET_COUNT`` 链读在同一处）；空预选集 = 无目标的响应使用（无懈一类的形状）；
    - ``DODGE``：无目标的 ``UseCard``（真实【闪】的候选正是这么生成的）；
    - ``NULLIFY``：**无懈的收集式窗口**里的无目标 ``UseCard``（huo-wolong-yanliang 票 03
      读法 A）：真实【无懈可击】的候选正是这么生成的，转化牌只在窗口里记一条**声明**，
      由赢家分支统一使用。牌名闸门 = :func:`~sgs.effects.satisfies_required`。

    **本函数只读状态、不移动任何牌**：来源牌的移出由 :func:`virtual_candidates` 在整批
    候选算完之后统一做一次（否则先算的那组候选会改变后算那组看到的目标状态）。
    """
    if ctx.scope is CandidateScope.PLAY:
        if check_count(state, seat, card):
            return []
        pool = check_targets(state, seat, card)
        behavior = behavior_for(card)
        rule = behavior.target_rule
        # **目标数区间要照真牌摊开**（huo-mobile-batch 票 02）：【铁索连环】是
        # ``min_targets=1 / max_targets=2``，真牌走 ``_play_phase`` 的
        # ``rule.target_range`` ⇒ 1 个与 2 个目标的组合都在候选里。转化产物的第一张
        # 区间牌就是【连环】，若这里仍按"至少 N 个"取单值，1–2 名目标会被压成"恰好 1 名"
        # ——与真牌不是同一条合法性检测。
        low, high = rule.target_range(len(pool))
        if low <= 0 or high < low:
            return []
        if rule.count is TargetCount.RATED and not rule.is_range:
            high = apply_modifiers(
                ModifierPoint.TARGET_COUNT,
                state,
                ModifierContext(subject=seat, card=card),
                high,
            )
        return [
            UseCard(card=card, targets=combo, answers_seq=ctx.answers_seq)
            for combo in target_combos(
                pool, low, high, ordered=behavior.preserve_target_order
            )
        ]
    if ctx.scope is CandidateScope.DODGE:
        targets = tuple((target,) for target in check_targets(state, seat, card)) or ((),)
        return [
            UseCard(card=card, targets=combo, answers_seq=ctx.answers_seq)
            for combo in targets
        ]
    if ctx.scope is CandidateScope.RESPOND:
        if ctx.respond_mode is RespondMode.USE:
            if check_count(state, seat, card):
                return []
            if ctx.respond_targets:
                # a 可达性（W5-02b）：forced_use_targets 只作"够不够得着固定 Bn"的判据，
                # **不再作枚举池**（W7-01）——否则池至多 {Bn}，额外目标永远进不来。
                if not forced_use_targets(state, seat, card, ctx.respond_targets):
                    # 目标已由窗口固定（借刀的 Bn）而这次转化够不着它 ⇒ 没有候选；
                    # 不退回"无目标使用"——那是无懈一类的形状，会造出一条打向空气的【杀】。
                    return []
                bn = ctx.respond_targets[0]
                pool = check_targets(state, seat, card)
                if bn not in pool:
                    # **不是**上面那条的重复（D2）：forced_use_targets 只证明预选集里
                    # **有**一个座位够得着，多座位载荷下它可能不是 Bn；Bn 自己仍须在池里。
                    # 在结算时已不合法（TARGET_POOL 链删掉了它）⇒ 这张牌没有候选。
                    return []
                count = 1  # 被迫使用窗口的 base = 1（固定 Bn），不是池大小
                high = count
                if behavior_for(card).target_rule.count is TargetCount.RATED:
                    high = apply_modifiers(
                        ModifierPoint.TARGET_COUNT,
                        state,
                        ModifierContext(
                            subject=seat,
                            card=card,
                            respond_required=ctx.respond_required,
                            respond_mode=ctx.respond_mode,
                            respond_targets=(bn,),
                        ),
                        count,
                    )
                combos = target_combos(pool, count, high, required=(bn,))
            else:
                pool = check_targets(state, seat, card)
                combos = tuple((target,) for target in pool) if pool else ((),)
            return [
                UseCard(card=card, targets=combo, answers_seq=ctx.answers_seq)
                for combo in combos
            ]
        return [PlayCard(card=card, answers_seq=ctx.answers_seq)]
    if ctx.scope is CandidateScope.NULLIFY:
        # **无懈的收集式窗口**（huo-wolong-yanliang 票 03 读法 A，用户 2026-11-06 裁）：
        # 转化族在这一档的产物是**无目标**的使用——真实【无懈可击】的候选正是这么来的；
        # 它在窗口里只算一条**声明**（``_collected_response_window`` 的
        # ``response_declared``），此刻不耗牌、不压帧，收齐后由赢家分支统一 ``start_use``。
        #
        # 牌名闸门与 ``RESPOND`` 分支共用 :func:`~sgs.effects.satisfies_required`：
        # 产物牌型必须当得了窗口要的那一张（本窗口的 ``respond_required`` 恒 = 牌名 id）。
        # **不检查次数**：无懈没有使用次数上限，转化牌与真实牌同一条链（规则 §16.4）。
        if ctx.respond_required is None or not satisfies_required(card, ctx.respond_required):
            return []
        return [UseCard(card=card, targets=(), answers_seq=ctx.answers_seq)]
    return []


def virtual_candidates(
    state: GameState,
    ctx: Any,
    *,
    target_type_id: str,
    sources: tuple[tuple[int, ...], ...],
    scopes: tuple[CandidateScope, ...],
) -> list[Any]:
    """把 ``sources`` 逐组转成 ``target_type_id`` 的候选（票 03 的**唯一**候选构造子）。

    ``sources`` 是**已经筛好的**来源组合（每组的张数就是本次转化的来源张数）；
    本函数只做"组合 → 虚拟牌 → 行动"的三段搬运，不重复任何筛选判据——每个技能一个
    ``CANDIDATES`` 回调声明"源筛选 + 目标牌名"，谓词**内联在回调里**（票 03 §要做什么 3）。

    **本函数只读状态、一张牌都不搬**（票 14 收口）：来源牌（含装备区 / 判定区的）由
    :meth:`~sgs.engine.game.Engine._move_virtual_sources` 在**使用链**里经
    ``move_in_frame`` 搬到处理区再进弃牌堆——那条路径才记 delta。早先在这里就地
    ``move_card`` 不落记录，会让记录流重建的牌堆与活状态分叉（票 14 的 fuzz 开启武将后
    实测：从装备区转化【杀】后，下一次从弃牌堆的移动对不上重建结果）。

    来源张数超过**该产物所在段**的上界（108 张档的共享段 ⇒ 7、``DISMANTLEMENT`` 段 ⇒ 6；
    160 张档 ⇒ 6，与 :func:`transformed_card` 读同一个 :func:`kind_source_ceiling`）时在
    这里就抛 :class:`VirtualSourceError`，不把越界组放行到构造器才失败。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return []
    if ctx.scope not in scopes:
        return []
    ceiling = kind_source_ceiling(int(_kind_of(target_type_id)), len(state.cards))
    actions: list[Any] = []
    for group in sources:
        _validate_source_count(group, max_sources=ceiling, target="virtual_candidates")
        card = transformed_card(state, group, target_type_id)
        if is_slash(behavior_for(card)) and any(
            # 【定乱】牌不能作为转化【杀】的源牌（R4=A；**多来源**也算——丈八的两张里
            # 任意一张带标记，整组「当【杀】」不合法）。
            forbidden_as_slash(state, state.card(card_id))
            for card_id in group
        ):
            continue
        if ctx.scope is CandidateScope.RESPOND and (
            ctx.respond_required is None
            or not satisfies_required(card, ctx.respond_required)
        ):
            # 牌名闸门（M5-05b）：响应窗口要的是 ``respond_required``，**产物牌型**必须
            # 当得了它；判据与 ``effects/actions._required_hand`` 共用
            # :func:`~sgs.effects.satisfies_required`，故【杀】族按共享计数键放宽
            # （朱雀羽扇的火【杀】产物因此能打向南蛮 / 决斗 / 借刀），其余牌名逐字相等。
            continue
        actions.extend(virtual_card_use(state, ctx.subject, card, ctx))
    return actions


def grouped_candidates(
    state: GameState,
    ctx: Any,
    *,
    target_type_id: str,
    cards: Sequence[int],
    group_size: int,
    scopes: tuple[CandidateScope, ...],
) -> list[Any]:
    """把 ``cards`` 按 ``group_size`` 分组后交给 :func:`virtual_candidates`。

    【丈八蛇矛】的"两张手牌"与 H 族的"一张牌"共用这一处**组合**，故"每个技能一个
    ``CANDIDATES`` 回调"只是选一组参数，不必各写一份组合逻辑。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return []
    groups = tuple(combinations(cards, group_size))
    return virtual_candidates(
        state, ctx, target_type_id=target_type_id, sources=groups, scopes=scopes
    )


def single_conversion(
    state: GameState,
    ctx: Any,
    *,
    target_type_id: str,
    cards: Sequence[int],
    scopes: tuple[CandidateScope, ...],
) -> list[Any]:
    """单源转化（H 族四技能）：每张筛好的牌各作一张虚拟牌。"""
    return grouped_candidates(
        state,
        ctx,
        target_type_id=target_type_id,
        cards=cards,
        group_size=1,
        scopes=scopes,
    )


SourceFilter = Callable[[int], bool]
"""来源谓词：``card_id -> bool``（五个谓词全部内联在技能的 ``CANDIDATES`` 回调里）。"""

