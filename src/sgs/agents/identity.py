"""身份判定与身份信念（`ai-view` 票 07；spec §2.6）。

本模块交出**两个读出口**，决策面只读第一个：

1. :func:`marginals` —— 每座的**边缘后验** ``P(座位 = 身份)``，来自**精确联合后验**：
   用 :data:`~sgs.data.IDENTITY_ROSTERS` 穷举不矛盾的联合分配（本人已知、主公已明、
   阵亡者已亮明），矛盾分配置零，再乘各**公开行为事件**的似然因子、归一。4/5/6/7/8 人局
   的有效分配数 = 6/**12**/20/60/**105**（主公一席已明 ⇒ 剩下 n−1 席的多重集排列），
   是一次循环的量级；不上 NN / MCMC / CFR。
   :func:`~sgs.agents.heuristic._identity_relation` 的"未知座位"分支读它，
   返回 ``(P(敌), P(友))`` —— **返回值契约、读点、14 个调用点一字不改**。
2. :func:`identity_judgment` —— 从同一份边缘后验读出的 ``(身份标签, 置信度)``，
   只供**解释与稳定性判定**；决策面仍按连续权重，**不换成硬标签**（spec §2.6 的
   "硬标签是降低安全边际的改动，不做"）。

**似然表** :data:`BEHAVIOR_LIKELIHOODS`：``P(行为类 | 发起身份, 观察者身份, 先手方)``，
键 = ``(行为类, 先手方, 观察者身份)``、值 = 四发起身份各一个数。行为类由**公开事实**
机械派生（见 :data:`BEHAVIOR_CLASSES`），先手方 = 该事件的一对座位里**谁先动的手**
（:data:`CONTEXTS`）。

**为什么观察者身份也进表**：行为类里的 ``lord`` / ``me`` / ``other`` 是**公开可读**的
目标分类（主公恒亮明、自己恒可知）；不按目标**隐藏**身份分档——那在推理时读不到，
也会把"我看见的打法"错当成"按真值打的"（train/serve skew）。但"我挨打"这一档的
分布**随观察者自己的身份变**（主公被反贼打、反贼被主公侧打），不条件化就会把两群
事件混成一格（第一版读数只有 0.016 nats/座位步的根因）。故表按观察者身份分四组。
表用**多版本混合**的 AI 对局估计（工具 ``tools/sgs_train/likelihood.py``，不得用单一
策略估——DeepRole 的坑）：行为模型越像自己，越会被别的策略系统性惩罚。表本身可解释、
可复算：9 × 2 × 4 行、每行 4 个数 = **288 个数**，每行带样本量。另有第二张表
:data:`PRESSURE_LIKELIHOODS`（``public_counts`` 的 3 类拿牌 / 弃牌压力 × 4 观察者 ×
4 发起身份 = **48 个数**）：窗口只有 32 条事实，早年 / 平静期经常没有可用事件，全局长
的聚合计数补这一段；两张表**不混表**（逐事件与聚合计数的单位不同）。

**两道反自我实现的护栏**（spec §2.6）：

- **似然表按先手方条件化**：``P(行为 | ..., 先手方)`` 两行分开估；
- **干预折扣**：我在之前 :data:`_RETALIATION_WINDOW` 条公开事实内先对某座位动过敌意动作 ⇒
  它对我（观察者）的反击**不构成证据**，该条观测的对数似然按
  :data:`RETALIATION_DISCOUNT` 缩权（位移显著小于同形但无先手的事件）。

**先手窗口的粒度**：公开面**没有**全局长在的回合边界事实（``turn_change`` 只在有轮次
技能的对局里落记录），故"之前 N 回合"落成"之前 N 条公开事实"——视图的
``public_history`` 本身恒 ≤ 32 条（:data:`~sgs.state.PUBLIC_SURFACE_RECENT_LIMIT`），
24 条约等于一回合。这是**有界公开面**能给出的口径，不另读引擎状态。

**读点**：只用 :class:`~sgs.state.PlayerView` 的公开面——``players`` 的
``identity`` / ``alive`` 与 ``public_history`` / ``public_counts``。不 import
``sgs.engine`` / ``sgs.skills``，不读 ``GameState``；本模块因此与启发式 AI 共享同一条
可见面纪律（``tests/test_source_discipline.py`` 的 ``heuristic`` 白名单只覆盖
``heuristic.py``，本模块另由 ``tests/test_identity_belief.py`` 的"构造局只差隐藏手牌"
用例钉住）。

**算不出就退回中性**：人数不在配比表、亮明身份不在配比里、阵亡却身份不可见、
配比与存活未知座位数对不上、或**没有任何公开行为证据** ⇒ :func:`joint_posterior` 返回
``None``，调用方走旧路径（:func:`~sgs.agents.heuristic._unknown_identity_remainder` 的
均匀后验 / ``(0, 0)``）。**完全不猜**，与 I1 同一条 fail-closed 口径。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from sgs.data import IDENTITY_ROSTERS, Identity
from sgs.state import PlayerView

__all__ = [
    "BEHAVIOR_CLASSES",
    "BEHAVIOR_LIKELIHOODS",
    "CONTEXTS",
    "IDENTITY_INDEX",
    "IDENTITY_ORDER",
    "ObservedBehavior",
    "PRESSURE_CLASSES",
    "PRESSURE_KINDS",
    "PRESSURE_LIKELIHOODS",
    "Posterior",
    "PressureObservation",
    "RETALIATION_DISCOUNT",
    "behavior_observations",
    "identity_judgment",
    "joint_posterior",
    "marginals",
    "pressure_observations",
    "relation_confidence",
    "relation_label",
]


# ---------------------------------------------------------------------------
# 身份顺序与公开行为类（闭集）
# ---------------------------------------------------------------------------

IDENTITY_ORDER: tuple[Identity, ...] = (
    Identity.LORD,
    Identity.LOYALIST,
    Identity.REBEL,
    Identity.RENEGADE,
)
"""似然表 / 边缘后验的**列顺序**（与 ``tools/ai_eval/replay.py`` 的读数顺序一致）。

只用这四值：PvE 的 ``ALLY`` / ``ENEMY`` 不进身份局配比，``joint_posterior`` 对它们
直接返回 ``None``（PvE 的敌友是"已亮明"那一档，不走概率档）。
"""

IDENTITY_INDEX: Mapping[Identity, int] = MappingProxyType(
    {identity: index for index, identity in enumerate(IDENTITY_ORDER)}
)
"""身份 → 表列号（``BEHAVIOR_LIKELIHOODS`` 的四元组 / 边缘后验共用一套顺序）。"""

BEHAVIOR_CLASSES: tuple[str, ...] = (
    "harm_lord",
    "harm_me",
    "harm_other",
    "help_lord",
    "help_me",
    "help_other",
    "pass_dying_lord",
    "pass_dying_me",
    "pass_dying_other",
)
"""公开行为类的**封闭清单**（新增类必须同时重估似然表）。

- ``harm``：敌意动作。牌类 = 伤害牌 / 有害锦囊的目标；变化事实 = 负 ``hp_change``；
- ``help``：有利动作。``桃`` / ``桃园结义`` 的目标；正 ``hp_change``；
- ``pass_dying``：濒死求桃窗口里**放弃**（"谁在濒死窗口没有出【桃】"）；
- 后缀 = 目标与**公开事实**的关系：``lord``（主公，恒亮明）/ ``me``（观察者本人）/
  ``other``（其余，隐藏身份）。**不按目标真实身份分档**：那在推理时读不到，会造出
  train/serve skew；隐藏身份的信息由观察者身份这一维承载（见模块 docstring）。
"""

CONTEXTS: tuple[str, ...] = ("first", "responder")
"""先手方（似然表的第二维）：

- ``first``：这一对座位里，没有更近的"对方先打过我"的公开事实；
- ``responder``：对方在 :data:`_RETALIATION_WINDOW` 条公开事实内**先动过手**，这一发是反击。
"""

PRESSURE_CLASSES: tuple[str, ...] = (
    "pressure_lord",
    "pressure_me",
    "pressure_other",
)
"""**聚合计数**派的三个行为类（`public_counts` 的长期证据）。

窗口只有 32 条事实，早年 / 平静期的窗口经常没有任何可用的敌意 / 有利事件；而
``PlayerView.public_counts`` 是**全局长**的聚合。牌被**拿走**（``move:LOSE`` /
``move:DISCARD``：拆 / 顺 / 决斗弃牌一类）对目标是有害的方向，且与身份强相关
（反贼拆主公 vs 忠臣拆主公）。这三类就是它的读法；顺序 / 先手方无从得知，故不参与
:data:`CONTEXTS` 的条件化。**与窗口类分开两张表**：聚合事件的"每局有没有"与逐事件
似然不是同一个条件分布，混进同一张表会让两边的分母互相污染。
"""

PRESSURE_KINDS: frozenset[str] = frozenset({"move:LOSE", "move:DISCARD"})
"""``public_counts`` 里算"拿牌 / 弃牌压力"的机械事实类（唯一真源 = 上文的语义登记）。"""

HARMFUL_CARD_TYPES: frozenset[str] = frozenset(
    {
        "arrow_barrage",
        "barbarian_invasion",
        "borrowed_sword",
        "dismantlement",
        "duel",
        "fire_attack",
        "indulgence",
        "lightning",
        "snatch",
        "supply_shortage",
        "slash",
        "fire_slash",
        "thunder_slash",
    }
)
"""**敌意动作**的牌型闭集：伤害牌（含属性 / 转化产物按 ``card.type`` 归族）＋有害锦囊。

牌名真源在启发式侧（:data:`~sgs.agents.heuristic._NULLIFY_HARMFUL_TRICKS`）；本模块
不 import ``heuristic``（会成环），故字面表在这里另立一份，
``tests/test_identity_belief.py`` 双向核对两张表（伤害牌那一半由 ``sgs.effects`` 的
``is_slash`` 核），漂移即红。
"""

HELPFUL_CARD_TYPES: frozenset[str] = frozenset({"peach", "peach_garden"})
"""**有利动作**的牌型闭集：单体【桃】与群体【桃园结义】（按目标逐个记一次）。"""

_RETALIATION_WINDOW = 24
"""干预折扣的"之前 N 条公开事实"（见模块 docstring：公开面无回合边界事实）。"""

RETALIATION_DISCOUNT = 0.25
"""反击观测的对数似然缩权：``log L × 0.25`` ⇒ 后验位移显著小于同形的无先手事件。

**不置零**：置零等于宣称"反击与身份无关"，而反击率在身份间仍有差（主公侧 / 反贼侧
的报复动机不同）；缩权保留方向、去掉大头。
"""

_LIKELIHOOD_SCALE = 1.0
"""似然因子的**温度**：``log L × scale``。``1.0`` = 表原样。

调它 = 在"证据够不够"上做一次标定（ECE 的主旋钮）。**判决批不得用于调参**（spec §6）：
开发批上调、冻结后只在判决批上读数；改动必须同时改这里的注释与台账。
"""

_PASS_DYING_WINDOW = "PEACH"
"""濒死求桃窗口的 ``QueryKind`` 值（``PublicEvent.window``）。"""

_DYING_HP = 0
"""濒死判据：``hp_change`` 的 ``after <= 0``（与引擎的濒死入口同一条事实）。"""


# ---------------------------------------------------------------------------
# 公开行为观测
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObservedBehavior:
    """一条**公开行为观测**：``(事件锚点, 发起座位, 目标座位, 行为类, 先手方, 我的先手)``。

    冻结值类型 + ``slots``：一次投影里最多几十条、每询问现算，构造要便宜；它也是
    :func:`joint_posterior` 的缓存键（可哈希、逐字节可比）。
    """

    seq: int
    actor: int
    target: int
    behavior: str
    context: str
    viewer_initiated: bool = False


def _lord_seat(view: PlayerView) -> int | None:
    """场上**已亮明**的主公座位；没有（人为局面 / PvE）⇒ ``None``。"""
    for player in view.players:
        if player.identity is Identity.LORD:
            return int(player.seat)
    return None


def _card_type(payload: Any) -> str | None:
    """``UseCard`` 载荷里的牌型（不是 ``UseCard`` ⇒ ``None``）。"""
    card = getattr(payload, "card", None)
    return None if card is None else str(card.type)


def _hostile_targets(payload: Any) -> tuple[int, ...]:
    """这条 ``UseCard`` 对哪些座位**敌意**（牌型闭集 + 借刀只算"附带指定"）。"""
    card_type = _card_type(payload)
    if card_type not in HARMFUL_CARD_TYPES:
        return ()
    targets = tuple(int(seat) for seat in getattr(payload, "targets", ()) or ())
    if card_type == "borrowed_sword":
        # 借刀：targets = (持刀者 A,)（被逼去打人），designations = (挨打者 Bn,)。对 Bn
        # 才是敌意；把 A 也算进去会让"逼敌人打敌人"被读成"我打敌人"。
        return tuple(int(seat) for seat in getattr(payload, "designations", ()) or ())
    return targets


def _hostile_pairs(history: Sequence[Any]) -> tuple[tuple[int, int, int], ...]:
    """窗口里全部**敌意**事实的 ``(下标, 发起座位, 目标座位)``（先手方的唯一判据）。

    只收"有发起座位、目标不是自己"的那几对；``hp_change`` 按**实际减体力**算，牌类按
    :data:`HARMFUL_CARD_TYPES` 算。同一条事实可能被记两遍（使用答案 + 落账伤害），
    这在先手判定里无害：两次都是"这一对之间先动过手"。
    """
    pairs: list[tuple[int, int, int]] = []
    for index, event in enumerate(history):
        actor = event.actor
        if actor is None:
            continue
        actor = int(actor)
        if event.fact_class == "answer:UseCard":
            for target in _hostile_targets(event.payload):
                if target != actor:
                    pairs.append((index, actor, target))
        elif event.fact_class == "delta:hp_change":
            payload = event.payload
            seat = getattr(payload, "seat", None)
            if seat is None:
                continue
            seat = int(seat)
            if seat != actor and int(getattr(payload, "amount", 0)) < 0:
                pairs.append((index, actor, seat))
    return tuple(pairs)


def _context(
    index: int,
    actor: int,
    target: int,
    hostile: Sequence[tuple[int, int, int]],
) -> str:
    """``actor → target`` 在 ``index`` 这一条上的先手方（只看窗口内、更早的事实）。"""
    start = index - _RETALIATION_WINDOW
    reverse = -1
    direct = -1
    for at, first, second in hostile:
        if at >= index:
            break
        if at < start:
            continue
        if first == target and second == actor:
            reverse = at
        elif first == actor and second == target:
            direct = at
    if reverse >= 0 and reverse > direct:
        return "responder"
    return "first"


def _viewer_struck_first(
    index: int,
    viewer: int,
    actor: int,
    hostile: Sequence[tuple[int, int, int]],
) -> bool:
    """``index`` 之前、窗口内，**观察者**是否先对 ``actor`` 动过敌意动作（干预折扣的判据）。

    与 :func:`_context` 共用同一份 ``hostile`` 序；区别只在"谁先"的方向：这里问的是
    ``viewer → actor`` 是否比反向更近（:func:`_context` 问的是对方是否先动过手）。
    """
    start = index - _RETALIATION_WINDOW
    direct = -1
    reverse = -1
    for at, first, second in hostile:
        if at >= index:
            break
        if at < start:
            continue
        if first == viewer and second == actor:
            direct = at
        elif first == actor and second == viewer:
            reverse = at
    return direct >= 0 and direct > reverse


def _dying_seat(history: Sequence[Any], index: int) -> int | None:
    """``index`` 之前最近一个**被打到濒死**的座位（濒死求桃窗口里由它回答"谁濒死"）。

    ``pass`` / ``Pass`` 的公开载荷没有目标（动作本身就没有 ``targets``），故濒死者只能从
    **更早的变化事实**读回：最近一条负 ``hp_change`` 且 ``after <= 0``。读不到 ⇒ ``None``
    （这条放弃不计入任何行为类，不猜）。
    """
    for at in range(index - 1, -1, -1):
        event = history[at]
        if event.fact_class != "delta:hp_change":
            continue
        payload = event.payload
        if int(getattr(payload, "amount", 0)) < 0 and int(
            getattr(payload, "after", 1)
        ) <= _DYING_HP:
            seat = getattr(payload, "seat", None)
            return None if seat is None else int(seat)
    return None


def _behavior_pairs(
    history: Sequence[Any], index: int, event: Any
) -> tuple[tuple[int, str], ...]:
    """一条公开事实派生出的 ``(目标座位, 行为类前缀)`` 列表（顺序 = 载荷顺序）。"""
    if event.fact_class == "answer:UseCard":
        payload = event.payload
        if _card_type(payload) in HARMFUL_CARD_TYPES:
            return tuple((target, "harm") for target in _hostile_targets(payload))
        if _card_type(payload) in HELPFUL_CARD_TYPES:
            return tuple(
                (int(target), "help")
                for target in getattr(payload, "targets", ()) or ()
            )
        return ()
    if event.fact_class == "delta:hp_change":
        payload = event.payload
        seat = getattr(payload, "seat", None)
        if seat is None:
            return ()
        amount = int(getattr(payload, "amount", 0))
        if amount < 0:
            return ((int(seat), "harm"),)
        if amount > 0:
            return ((int(seat), "help"),)
        return ()
    if event.fact_class == "answer:Pass" and event.window == _PASS_DYING_WINDOW:
        dying = _dying_seat(history, index)
        return () if dying is None else ((dying, "pass_dying"),)
    return ()


def _behavior_class(prefix: str, target: int, viewer: int, lord: int | None) -> str:
    """``行为前缀`` + 目标与**公开事实**的关系后缀（``lord`` / ``me`` / ``other``）。"""
    if target == viewer:
        where = "me"
    elif lord is not None and target == lord:
        where = "lord"
    else:
        where = "other"
    return f"{prefix}_{where}"


def behavior_observations(view: PlayerView) -> tuple[ObservedBehavior, ...]:
    """把当前视图的 ``public_history`` 摊成**公开行为观测**（似然表的读方唯一入口）。

    只读公开事实：``actor`` / ``targets`` / ``fact_class`` / ``window`` / 公开载荷。
    自己发起的事实不收（观察者知道自己的身份，读了也只会乘一个常数）；目标 = 发起者的
    事实不收（没有"对谁"这一说）。顺序 = 事实序 × 载荷序，确定性。

    同一条 ``UseCard`` 与它造成的 ``hp_change`` 会各记一条（"打出去"与"打中了"都是
    公开行为）；表在**同一口径**下估计，故不引入 train/serve 差异。
    """
    history = view.public_history
    if not history:
        return ()
    viewer = int(view.seat)
    lord = _lord_seat(view)
    hostile = _hostile_pairs(history)
    observations: list[ObservedBehavior] = []
    for index, event in enumerate(history):
        actor = event.actor
        if actor is None:
            continue
        actor = int(actor)
        if actor == viewer:
            continue
        for target, prefix in _behavior_pairs(history, index, event):
            if target == actor:
                continue
            context = _context(index, actor, target, hostile)
            # 干预折扣只对**打到我身上的敌意动作**生效：我的先手是那一对的另一种先手方。
            viewer_initiated = (
                prefix == "harm"
                and target == viewer
                and _viewer_struck_first(index, viewer, actor, hostile)
            )
            observations.append(
                ObservedBehavior(
                    seq=int(event.seq),
                    actor=actor,
                    target=target,
                    behavior=_behavior_class(prefix, target, viewer, lord),
                    context=context,
                    viewer_initiated=viewer_initiated,
                )
            )
    return tuple(observations)


@dataclass(frozen=True, slots=True)
class PressureObservation:
    """一条**聚合计数**观测：``(发起座位, 压力类, 次数)``。

    ``count`` 是该（发起, 目标）对在 ``public_counts`` 里同一类的次数；后验按
    ``count × log P(类 | 发起身份, 观察者身份)`` 计权（同一类的多次事件共享同一个因子）。
    """

    actor: int
    behavior: str
    count: int


def pressure_observations(view: PlayerView) -> tuple[PressureObservation, ...]:
    """从 ``public_counts`` 读**长期**拿牌 / 弃牌压力（窗口类之外的第二个证据源）。

    只收 ``owner`` / ``seat`` 都有值、且不是同一座位的格子（自己弃自己的牌没有"对谁"）；
    自己发起的事实不收（理由同 :func:`behavior_observations`）。顺序 = 计数表首次出现序，
    同类合并、按 ``(actor, behavior)`` 归并以保持确定性。
    """
    counts: dict[tuple[int, str], int] = {}
    order: list[tuple[int, str]] = []
    viewer = int(view.seat)
    lord = _lord_seat(view)
    for entry in view.public_counts:
        if entry.kind not in PRESSURE_KINDS:
            continue
        owner = entry.owner
        seat = entry.seat
        if owner is None or seat is None:
            continue
        owner = int(owner)
        seat = int(seat)
        if owner == seat or owner == viewer:
            continue
        behavior = _behavior_class("pressure", seat, viewer, lord)
        key = (owner, behavior)
        if key not in counts:
            order.append(key)
            counts[key] = 0
        counts[key] += int(entry.count)
    return tuple(
        PressureObservation(actor=actor, behavior=behavior, count=counts[(actor, behavior)])
        for actor, behavior in order
    )


# ---------------------------------------------------------------------------
# 精确联合后验
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Posterior:
    """精确联合后验：``assignments`` 与 ``weights`` 逐项对应（``Σ weights == 1``）。

    只装在**不矛盾**的联合身份分配上（本人已知、主公已明、阵亡者已亮明、配比成立）；
    权重 = 先验（多重集排列，等权）× 各公开行为事件的似然因子。测试直接读它核
    "``Σ 分配质量 == 1``"与"已亮明 / 已阵亡座位的边缘恒 0/1"。
    """

    assignments: tuple[tuple[Identity, ...], ...]
    weights: tuple[float, ...]

    def marginal(self, seat: int) -> tuple[float, ...]:
        """``seat`` 的边缘后验（列顺序 = :data:`IDENTITY_ORDER`）。"""
        totals = [0.0, 0.0, 0.0, 0.0]
        for assignment, weight in zip(self.assignments, self.weights, strict=True):
            totals[IDENTITY_INDEX[assignment[seat]]] += weight
        return tuple(totals)


@dataclass(frozen=True, slots=True)
class IdentityJudgment:
    """``(身份标签, 置信度)``：只供解释 / 稳定性判定，**不进决策**（spec §2.6）。"""

    label: Identity
    confidence: float
    marginal: tuple[float, ...]


def _multiset_assignments(
    remaining: tuple[Identity, ...],
) -> tuple[tuple[Identity, ...], ...]:
    """``remaining`` 的**不重复**全排列（确定性顺序 = 首次出现序的递归枚举）。

    直接 ``itertools.permutations`` 会把两个同身份座位算成两种分配（8 人局 7! = 5040
    而有效分配只有 105 种），既慢又让测试的"分配数"对不上票面。这里按多重集计数递归，
    产出恰为票面的 6 / 12 / 20 / 60 / 105 种。
    """
    order: list[Identity] = []
    counts: dict[Identity, int] = {}
    for identity in remaining:
        if identity not in counts:
            order.append(identity)
            counts[identity] = 0
        counts[identity] += 1
    total = len(remaining)
    results: list[tuple[Identity, ...]] = []
    prefix: list[Identity] = []

    def walk() -> None:
        if len(prefix) == total:
            results.append(tuple(prefix))
            return
        for identity in order:
            if counts[identity] == 0:
                continue
            counts[identity] -= 1
            prefix.append(identity)
            walk()
            prefix.pop()
            counts[identity] += 1

    walk()
    return tuple(results)


def _fixed_and_remaining(
    view: PlayerView,
) -> tuple[tuple[Identity | None, ...], tuple[int, ...], tuple[Identity, ...]] | None:
    """``(固定身份逐座, 未知存活座位升序, 剩余多重集)``；任一"算不出" ⇒ ``None``。"""
    roster = IDENTITY_ROSTERS.get(len(view.players))
    if roster is None:
        return None
    remaining = list(roster)
    fixed: list[Identity | None] = []
    hidden: list[int] = []
    for player in view.players:
        identity = player.identity
        if identity is not None:
            if identity not in remaining:
                return None
            remaining.remove(identity)
            fixed.append(identity)
        elif player.alive:
            fixed.append(None)
            hidden.append(int(player.seat))
        else:
            # 阵亡却身份不可见：投影上不该发生（阵亡即亮明）⇒ 不猜。
            return None
    if len(remaining) != len(hidden):
        return None
    return tuple(fixed), tuple(hidden), tuple(remaining)


@lru_cache(maxsize=32)
def _posterior_for_key(
    seat: int,
    fixed: tuple[Identity | None, ...],
    hidden: tuple[int, ...],
    observations: tuple[ObservedBehavior, ...],
    pressures: tuple[PressureObservation, ...],
) -> Posterior | None:
    """按观察序列算一次后验（缓存：同一询问里 ``_identity_relation`` 会被调十几次）。"""
    roster = IDENTITY_ROSTERS.get(len(fixed))
    if roster is None:
        return None
    viewer = fixed[seat]
    if viewer is None:
        return None
    remaining = list(roster)
    for identity in fixed:
        if identity is None:
            continue
        if identity not in remaining:
            return None
        remaining.remove(identity)
    if len(remaining) != len(hidden):
        return None

    informative = tuple(
        observation
        for observation in observations
        if observation.actor != seat and fixed[observation.actor] is None
    )
    informative_pressures = tuple(
        pressure
        for pressure in pressures
        if pressure.actor != seat and fixed[pressure.actor] is None
    )
    if not informative and not informative_pressures:
        # 没有公开行为证据 ⇒ 走旧的均匀路径（逐值相等，且浮点上仍是精确的 0/1 边缘）。
        return None

    assignments: list[tuple[Identity, ...]] = []
    for permutation in _multiset_assignments(tuple(remaining)):
        assembled = list(fixed)
        for index, at in enumerate(hidden):
            assembled[at] = permutation[index]
        assignments.append(tuple(assembled))
    if not assignments:
        return None

    log_weights: list[float] = []
    for assignment in assignments:
        total = 0.0
        for observation in informative:
            actor = assignment[observation.actor]
            probability = BEHAVIOR_LIKELIHOODS[
                (observation.behavior, observation.context, viewer.value)
            ][IDENTITY_INDEX[actor]]
            factor = _LIKELIHOOD_SCALE
            if observation.viewer_initiated:
                factor *= RETALIATION_DISCOUNT
            total += factor * math.log(probability)
        for pressure in informative_pressures:
            actor = assignment[pressure.actor]
            probability = PRESSURE_LIKELIHOODS[
                (pressure.behavior, viewer.value)
            ][IDENTITY_INDEX[actor]]
            total += (
                pressure.count * _LIKELIHOOD_SCALE * math.log(probability)
            )
        log_weights.append(total)

    top = max(log_weights)
    raw = [math.exp(value - top) for value in log_weights]
    normalizer = sum(raw)
    if normalizer <= 0.0:  # pragma: no cover - 表值恒 > 0，防御分支
        return None
    weights = tuple(value / normalizer for value in raw)
    if max(weights) - min(weights) < 1e-12:
        # 全部等权 = 证据没有推动任何分配（表行对四身份同值 / 观测全被折扣）；
        # 交给均匀路径，保持"无证据"的逐值行为。
        return None
    return Posterior(assignments=tuple(assignments), weights=weights)


def joint_posterior(view: PlayerView) -> Posterior | None:
    """``view`` 上的精确联合后验；算不出 / 无证据 ⇒ ``None``（调用方退回均匀后验）。"""
    resolved = _fixed_and_remaining(view)
    if resolved is None:
        return None
    fixed, hidden, _remaining = resolved
    if fixed[view.seat] is None:
        # 观察者不知道自己的身份：无法条件化"谁对我出手"，不猜。
        return None
    if not hidden:
        # 全场已明：只有一个分配，但没有"未知"要估——按无证据处理（调用方的已明分支
        # 本来就不会走到这里）。
        return None
    return _posterior_for_key(
        int(view.seat),
        fixed,
        hidden,
        behavior_observations(view),
        pressure_observations(view),
    )


def marginals(view: PlayerView) -> tuple[tuple[float, ...], ...] | None:
    """每座的边缘后验（列顺序 = :data:`IDENTITY_ORDER`）；算不出 ⇒ ``None``。

    没有公开行为证据时返回**均匀后验**的边缘（与 I1 的配比均分逐值相等），故"尺子"的
    模型行与基线行在有证据之前**逐值相同**——差异只来自公开行为。
    """
    resolved = _fixed_and_remaining(view)
    if resolved is None:
        return None
    fixed, hidden, remaining = resolved
    if fixed[view.seat] is None:
        return None
    if not hidden:
        return tuple(
            tuple(1.0 if identity == member else 0.0 for identity in IDENTITY_ORDER)
            for member in fixed
        )
    posterior = joint_posterior(view)
    if posterior is not None:
        return tuple(posterior.marginal(seat) for seat in range(len(fixed)))
    # 均匀后验的解析边缘：每个未知座位的 P(c) = 剩余多重集里 c 的份数 ÷ 未知座位数。
    # 用解析式而不是枚举，是为了让"无证据"这一档与 I1 的读数**逐值相等**（含浮点）。
    totals = tuple(
        remaining.count(identity) / len(hidden) for identity in IDENTITY_ORDER
    )
    return tuple(
        tuple(1.0 if identity == member else 0.0 for identity in IDENTITY_ORDER)
        if member is not None
        else totals
        for member in fixed
    )


# ---------------------------------------------------------------------------
# 判定出口（标签 + 置信度；不进决策）
# ---------------------------------------------------------------------------


def identity_judgment(view: PlayerView, seat: int) -> IdentityJudgment | None:
    """``seat`` 的 ``(身份标签, 置信度)``：边缘后验最大项 + 它的概率。

    置信度 = 最大边缘概率。这是**解释 / 稳定性判定**的读出口；决策面读的是
    ``(P(敌), P(友))`` 连续权重（见 :func:`~sgs.agents.heuristic._identity_relation`）。
    """
    if not 0 <= seat < len(view.players):
        return None
    values = marginals(view)
    if values is None:
        return None
    row = values[seat]
    best = max(range(len(row)), key=row.__getitem__)
    return IdentityJudgment(
        label=IDENTITY_ORDER[best], confidence=row[best], marginal=row
    )


_REL_ENEMY = "敌"
_REL_ALLY = "友"
_REL_NEUTRAL = "中立"
_REL_UNDECIDED = "未定"
"""关系判定的闭集（与 ``tools/ai_eval/replay.py`` 的读数同一套字符串）。"""


def relation_label(enemy: float, ally: float) -> str:
    """``(P敌, P友)`` → 关系标签（敌 / 友 / 中立；敌友平票才写「未定」）。"""
    neutral = max(0.0, 1.0 - enemy - ally)
    best = max(enemy, ally, neutral)
    if best == 0.0:
        return _REL_NEUTRAL
    winners = [
        name
        for name, value in (
            (_REL_ENEMY, enemy),
            (_REL_ALLY, ally),
            (_REL_NEUTRAL, neutral),
        )
        if value == best
    ]
    if len(winners) == 1:
        return winners[0]
    if _REL_NEUTRAL in winners:
        return _REL_NEUTRAL
    return _REL_UNDECIDED


def relation_confidence(enemy: float, ally: float) -> float:
    """``(P敌, P友)`` 的置信度 = 三类里最大的一项（与关系标签同一条次序）。"""
    return max(enemy, ally, max(0.0, 1.0 - enemy - ally))


# ---------------------------------------------------------------------------
# 似然表（由 tools/sgs_train/likelihood.py 估计；改表必须重跑工具并重记样本量）
# ---------------------------------------------------------------------------

BEHAVIOR_LIKELIHOODS: Mapping[
    tuple[str, str, str], tuple[float, float, float, float]
] = MappingProxyType(
    {
        # 由 tools/sgs_train/likelihood.py 估计：234 局 / 57050 条观测 / 版本混合 ai-v0 / ai-v5 / ai-v10
        # 每键 = P(行为类 | 发起身份, 观察者身份, 先手方)；值按 IDENTITY_ORDER（发起）
            ("harm_lord", "first", "lord"): (0.111111, 0.000170, 0.000088, 0.000147),  # 分母 0 / 2941 / 5693 / 3394
            ("harm_lord", "first", "loyalist"): (0.000142, 0.111111, 0.315645, 0.176211),  # 分母 3509 / 0 / 4537 / 2484
            ("harm_lord", "first", "rebel"): (0.000073, 0.159602, 0.313499, 0.177743),  # 分母 6827 / 4466 / 4281 / 4848
            ("harm_lord", "first", "renegade"): (0.000129, 0.181616, 0.329596, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("harm_me", "first", "lord"): (0.111111, 0.183500, 0.365511, 0.246726),  # 分母 0 / 2941 / 5693 / 3394
            ("harm_me", "first", "loyalist"): (0.245766, 0.111111, 0.181328, 0.235282),  # 分母 3509 / 0 / 4537 / 2484
            ("harm_me", "first", "rebel"): (0.258582, 0.257354, 0.184459, 0.249047),  # 分母 6827 / 4466 / 4281 / 4848
            ("harm_me", "first", "renegade"): (0.285402, 0.238307, 0.167224, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("harm_other", "first", "lord"): (0.111111, 0.593278, 0.419043, 0.528616),  # 分母 0 / 2941 / 5693 / 3394
            ("harm_other", "first", "loyalist"): (0.563968, 0.111111, 0.290983, 0.371107),  # 分母 3509 / 0 / 4537 / 2484
            ("harm_other", "first", "rebel"): (0.554710, 0.373001, 0.290398, 0.359505),  # 分母 6827 / 4466 / 4281 / 4848
            ("harm_other", "first", "renegade"): (0.535434, 0.359789, 0.289488, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("help_lord", "first", "lord"): (0.111111, 0.000170, 0.000088, 0.000147),  # 分母 0 / 2941 / 5693 / 3394
            ("help_lord", "first", "loyalist"): (0.000142, 0.111111, 0.012000, 0.036367),  # 分母 3509 / 0 / 4537 / 2484
            ("help_lord", "first", "rebel"): (0.000073, 0.027178, 0.011784, 0.034930),  # 分母 6827 / 4466 / 4281 / 4848
            ("help_lord", "first", "renegade"): (0.000129, 0.039887, 0.012183, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("help_me", "first", "lord"): (0.111111, 0.037854, 0.012549, 0.047227),  # 分母 0 / 2941 / 5693 / 3394
            ("help_me", "first", "loyalist"): (0.020350, 0.111111, 0.028074, 0.021901),  # 分母 3509 / 0 / 4537 / 2484
            ("help_me", "first", "rebel"): (0.022323, 0.024047, 0.038852, 0.024214),  # 分母 6827 / 4466 / 4281 / 4848
            ("help_me", "first", "renegade"): (0.020193, 0.022879, 0.031375, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("help_other", "first", "lord"): (0.111111, 0.056187, 0.080825, 0.053112),  # 分母 0 / 2941 / 5693 / 3394
            ("help_other", "first", "loyalist"): (0.051658, 0.111111, 0.061103, 0.041591),  # 分母 3509 / 0 / 4537 / 2484
            ("help_other", "first", "rebel"): (0.048672, 0.041494, 0.056119, 0.039670),  # 分母 6827 / 4466 / 4281 / 4848
            ("help_other", "first", "renegade"): (0.043344, 0.036242, 0.060270, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("pass_dying_lord", "first", "lord"): (0.111111, 0.000170, 0.000088, 0.000147),  # 分母 0 / 2941 / 5693 / 3394
            ("pass_dying_lord", "first", "loyalist"): (0.000142, 0.111111, 0.020148, 0.015471),  # 分母 3509 / 0 / 4537 / 2484
            ("pass_dying_lord", "first", "rebel"): (0.000073, 0.013309, 0.019951, 0.012880),  # 分母 6827 / 4466 / 4281 / 4848
            ("pass_dying_lord", "first", "renegade"): (0.000129, 0.016400, 0.022534, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("pass_dying_me", "first", "lord"): (0.111111, 0.015787, 0.026591, 0.020156),  # 分母 0 / 2941 / 5693 / 3394
            ("pass_dying_me", "first", "loyalist"): (0.027181, 0.111111, 0.026533, 0.026321),  # 分母 3509 / 0 / 4537 / 2484
            ("pass_dying_me", "first", "rebel"): (0.028764, 0.032547, 0.025085, 0.032045),  # 分母 6827 / 4466 / 4281 / 4848
            ("pass_dying_me", "first", "renegade"): (0.021479, 0.026524, 0.016712, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("pass_dying_other", "first", "lord"): (0.111111, 0.112884, 0.095217, 0.103722),  # 分母 0 / 2941 / 5693 / 3394
            ("pass_dying_other", "first", "loyalist"): (0.090650, 0.111111, 0.064186, 0.075748),  # 分母 3509 / 0 / 4537 / 2484
            ("pass_dying_other", "first", "rebel"): (0.086731, 0.071469, 0.059853, 0.069964),  # 分母 6827 / 4466 / 4281 / 4848
            ("pass_dying_other", "first", "renegade"): (0.093762, 0.078356, 0.070620, 0.111111),  # 分母 3883 / 2465 / 4633 / 0
            ("harm_lord", "responder", "lord"): (0.111111, 0.003460, 0.001105, 0.001898),  # 分母 0 / 140 / 448 / 259
            ("harm_lord", "responder", "loyalist"): (0.002770, 0.111111, 0.477046, 0.260274),  # 分母 176 / 0 / 246 / 105
            ("harm_lord", "responder", "rebel"): (0.001013, 0.346049, 0.524476, 0.242820),  # 分母 489 / 179 / 210 / 187
            ("harm_lord", "responder", "renegade"): (0.001681, 0.280000, 0.502959, 0.111111),  # 分母 293 / 108 / 249 / 0
            ("harm_me", "responder", "lord"): (0.111111, 0.370242, 0.653039, 0.590133),  # 分母 0 / 140 / 448 / 259
            ("harm_me", "responder", "loyalist"): (0.207756, 0.111111, 0.253493, 0.452055),  # 分母 176 / 0 / 246 / 105
            ("harm_me", "responder", "rebel"): (0.600811, 0.237057, 0.114219, 0.263708),  # 分母 489 / 179 / 210 / 187
            ("harm_me", "responder", "renegade"): (0.465546, 0.448889, 0.234714, 0.111111),  # 分母 293 / 108 / 249 / 0
            ("harm_other", "responder", "lord"): (0.111111, 0.508651, 0.290608, 0.332068),  # 分母 0 / 140 / 448 / 259
            ("harm_other", "responder", "loyalist"): (0.673130, 0.111111, 0.209581, 0.196347),  # 分母 176 / 0 / 246 / 105
            ("harm_other", "responder", "rebel"): (0.327254, 0.324251, 0.300699, 0.373368),  # 分母 489 / 179 / 210 / 187
            ("harm_other", "responder", "renegade"): (0.438655, 0.173333, 0.187377, 0.111111),  # 分母 293 / 108 / 249 / 0
            ("help_lord", "responder", "lord"): (0.111111, 0.003460, 0.001105, 0.001898),  # 分母 0 / 140 / 448 / 259
            ("help_lord", "responder", "loyalist"): (0.002770, 0.111111, 0.013972, 0.013699),  # 分母 176 / 0 / 246 / 105
            ("help_lord", "responder", "rebel"): (0.001013, 0.029973, 0.016317, 0.007833),  # 分母 489 / 179 / 210 / 187
            ("help_lord", "responder", "renegade"): (0.001681, 0.004444, 0.017751, 0.111111),  # 分母 293 / 108 / 249 / 0
            ("help_me", "responder", "lord"): (0.111111, 0.031142, 0.009945, 0.013283),  # 分母 0 / 140 / 448 / 259
            ("help_me", "responder", "loyalist"): (0.013850, 0.111111, 0.005988, 0.004566),  # 分母 176 / 0 / 246 / 105
            ("help_me", "responder", "rebel"): (0.019250, 0.013624, 0.002331, 0.023499),  # 分母 489 / 179 / 210 / 187
            ("help_me", "responder", "renegade"): (0.015126, 0.013333, 0.009862, 0.111111),  # 分母 293 / 108 / 249 / 0
            ("help_other", "responder", "lord"): (0.111111, 0.031142, 0.009945, 0.020873),  # 分母 0 / 140 / 448 / 259
            ("help_other", "responder", "loyalist"): (0.041551, 0.111111, 0.009980, 0.022831),  # 分母 176 / 0 / 246 / 105
            ("help_other", "responder", "rebel"): (0.013171, 0.002725, 0.006993, 0.023499),  # 分母 489 / 179 / 210 / 187
            ("help_other", "responder", "renegade"): (0.025210, 0.022222, 0.001972, 0.111111),  # 分母 293 / 108 / 249 / 0
            ("pass_dying_lord", "responder", "lord"): (0.111111, 0.003460, 0.001105, 0.001898),  # 分母 0 / 140 / 448 / 259
            ("pass_dying_lord", "responder", "loyalist"): (0.002770, 0.111111, 0.001996, 0.004566),  # 分母 176 / 0 / 246 / 105
            ("pass_dying_lord", "responder", "rebel"): (0.001013, 0.008174, 0.002331, 0.002611),  # 分母 489 / 179 / 210 / 187
            ("pass_dying_lord", "responder", "renegade"): (0.001681, 0.022222, 0.001972, 0.111111),  # 分母 293 / 108 / 249 / 0
            ("pass_dying_me", "responder", "lord"): (0.111111, 0.024221, 0.007735, 0.017078),  # 分母 0 / 140 / 448 / 259
            ("pass_dying_me", "responder", "loyalist"): (0.008310, 0.111111, 0.009980, 0.013699),  # 分母 176 / 0 / 246 / 105
            ("pass_dying_me", "responder", "rebel"): (0.021277, 0.019074, 0.016317, 0.023499),  # 分母 489 / 179 / 210 / 187
            ("pass_dying_me", "responder", "renegade"): (0.011765, 0.013333, 0.013807, 0.111111),  # 分母 293 / 108 / 249 / 0
            ("pass_dying_other", "responder", "lord"): (0.111111, 0.024221, 0.025414, 0.020873),  # 分母 0 / 140 / 448 / 259
            ("pass_dying_other", "responder", "loyalist"): (0.047091, 0.111111, 0.017964, 0.031963),  # 分母 176 / 0 / 246 / 105
            ("pass_dying_other", "responder", "rebel"): (0.015198, 0.019074, 0.016317, 0.039164),  # 分母 489 / 179 / 210 / 187
            ("pass_dying_other", "responder", "renegade"): (0.038655, 0.022222, 0.029586, 0.111111),  # 分母 293 / 108 / 249 / 0
    }
)
"""``P(行为类 | 发起身份, 观察者身份, 先手方)``：键 = ``(行为类, 先手方, 观察者身份)``。

值 = 四发起身份（:data:`IDENTITY_ORDER`）。**估计口径**（`tools/sgs_train/likelihood.py`）：
**234** 局 5 人身份局、**57,050** 条去重后的公开行为观测；版本按座位轮换
`ai-v0 / ai-v5 / ai-v10`（每个版本都坐过每个座位），不是单一策略的自画像。每行分母见
注释（发起 × 观察者 × 先手方），平滑 = 每格 +0.5。改表必须重跑工具并把新的样本量写进
票面 `## Answer` 与文件顶部的估计行（`tests/test_identity_belief.py` 核表结构 / 行和）。
"""

PRESSURE_LIKELIHOODS: Mapping[
    tuple[str, str], tuple[float, float, float, float]
] = MappingProxyType(
    {
        ("pressure_lord", "lord"): (0.333333, 0.001321, 0.000574, 0.000978),  # 分母 0 / 377 / 870 / 510
        ("pressure_lord", "loyalist"): (0.000987, 0.333333, 0.361702, 0.316312),  # 分母 505 / 0 / 680 / 351
        ("pressure_lord", "rebel"): (0.000484, 0.269841, 0.345871, 0.305048),  # 分母 1032 / 597 / 610 / 682
        ("pressure_lord", "renegade"): (0.000829, 0.277519, 0.350916, 0.333333),  # 分母 602 / 321 / 681 / 0
        ("pressure_me", "lord"): (0.333333, 0.291942, 0.430866, 0.421310),  # 分母 0 / 377 / 870 / 510
        ("pressure_me", "loyalist"): (0.324778, 0.333333, 0.251651, 0.341844),  # 分母 505 / 0 / 680 / 351
        ("pressure_me", "rebel"): (0.326560, 0.286550, 0.262469, 0.249451),  # 分母 1032 / 597 / 610 / 682
        ("pressure_me", "renegade"): (0.381939, 0.299225, 0.235165, 0.333333),  # 分母 602 / 321 / 681 / 0
        ("pressure_other", "lord"): (0.333333, 0.706737, 0.568560, 0.577713),  # 分母 0 / 377 / 870 / 510
        ("pressure_other", "loyalist"): (0.674235, 0.333333, 0.386647, 0.341844),  # 分母 505 / 0 / 680 / 351
        ("pressure_other", "rebel"): (0.672956, 0.443609, 0.391660, 0.445501),  # 分母 1032 / 597 / 610 / 682
        ("pressure_other", "renegade"): (0.617233, 0.423256, 0.413919, 0.333333),  # 分母 602 / 321 / 681 / 0
    }
)
"""``P(压力类 | 发起身份, 观察者身份)``：键 = ``(压力类, 观察者身份)``，值 = 四发起身份。

同一次估计的**第二张表**（``public_counts`` 的全局长压力，9 类窗口行为之外的补充）：
**234** 局里按局尾视图各计一次，行分母见注释。它不与窗口表混用——两种观测的单位不同
（逐事件 vs 全局长计数），混表会互相污染分母。
"""
