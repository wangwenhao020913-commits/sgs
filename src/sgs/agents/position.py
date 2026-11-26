"""局势打分（`ai-view` 票 09；spec §1 ④ / §2.2 第 2 层）：从**公开视角**读四类结局概率。

**读的是"这局最后谁赢"，不是"我这一步该做什么"**：同一次询问的全部候选共享同一个局面，
把读数加到候选分上是同一个常数、不改变 argmax（`GLOSSARY.md`「局势打分」）。所以本模块
**不进决策路径**，只给回放台 / 读数命令用。

四类结局是**互斥**的（和恒为 1）：

- ``lord``：主公方（主公 + 忠臣）胜；
- ``rebel``：反贼方胜；
- ``renegade``：内奸胜；
- ``draw``：平局 / 无人可胜（引擎 ``Outcome.result == "draw"`` 的那一档；正常身份局罕见，
  公式里给它一个接近 0 的底）。

**只用公开信息**：每个座位的体力 / 护甲 / 手牌数 / 装备（``PlayerPublic``），身份用
:func:`sgs.agents.identity.marginals` 的边缘后验（本人 / 主公 / 已阵亡已明，未知座位按
公开行为推断；无证据时退回配比均匀）。**不读**隐藏手牌、牌堆顺序、未亮明的身份。

**公式**（第一版是手写常量，不做训练；票面裁定"纯 Python 手写公式"）：

    strength(side) = Σ_{alive seat} P(seat ∈ side) × (hp + armor + w_hand·hand
                                                       + w_equip·equip + w_alive)
    logit(side)    = strength(side) / T + bias(side)

四个 logit（含 ``draw`` 的常数底）过一个 softmax。常量在下面集中定义，改它们要重跑
``tests/test_position_score.py`` 与票 09 的读数批。

**边界**：

- 身份后验算不出（PvE / 未知本人身份）⇒ 用可见身份的均匀摊派，**不猜**；
- 只剩一个阵营有存活座位 ⇒ 该阵营概率 1（胜负已定）；无人存活 ⇒ 平局 1；
- 纯函数：不写视图、不取随机数、不做 IO；同输入同输出（跨 ``PYTHONHASHSEED`` 逐字节一致）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sgs.data import IDENTITY_ROSTERS, Identity
from sgs.state import PlayerView

from . import identity as identity_model

__all__ = [
    "DRAW",
    "LORD",
    "POSITION_SIDES",
    "PositionReading",
    "REBEL",
    "RENEGADE",
    "read",
]

LORD = 0
REBEL = 1
RENEGADE = 2
DRAW = 3

POSITION_SIDES: tuple[str, ...] = ("lord", "rebel", "renegade", "draw")
"""四类的**报表顺序**（与 :class:`PositionReading.probabilities` 的列一一对应）。"""


# ---------------------------------------------------------------------------
# 手写常量（票 09 第一版；改它们 = 改读数，必须重跑票面的读数列）
# ---------------------------------------------------------------------------

_W_HP = 1.0
"""每点体力 / 护甲的权重。体力是场上最直接的实力量。"""

_W_HAND = 0.7
"""每张手牌的权重（未知牌按半张牌的防御 / 进攻价值折算）。"""

_W_EQUIP = 0.2
"""每件装备的权重（装备是公开的持续优势，但不如体力直接）。"""

_W_ALIVE = 1.0
"""每个存活座位的底分：活着本身就有价值（行动次数 / 目标数）。"""

_TEMPERATURE = 5.0
"""softmax 温度：把 strength 的分数差压到概率尺度。"""

_BIAS = (0.0, 0.0, -0.5, 0.0)
"""四类的先验偏置（``lord`` / ``rebel`` / ``renegade`` / ``draw``）。

内奸单独一个负偏置：它必须以一敌二、且要"最后一个活"，光比 strength 会高估它。平局的
偏置另由 :data:`_DRAW_FLOOR` 控制（见 :func:`_probabilities`）。
"""

_DRAW_FLOOR = 1e-6
"""平局概率的极小底：身份局正常不会平局，但四类要和为 1、且 log 要有限。"""


@dataclass(frozen=True, slots=True)
class PositionReading:
    """一次读数的四个概率（顺序 = :data:`POSITION_SIDES`，和 == 1）。"""

    probabilities: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        values = self.probabilities
        if len(values) != 4:
            raise ValueError(f"局势读数必须是四类：{values!r}")
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in values):
            raise ValueError(f"局势读数必须是 [0, 1] 内的有限值：{values!r}")
        # 和恒为 1 是票面的自洽判据；softmax + 归一化在 1e-15 内成立，给浮点留一格。
        if abs(sum(values) - 1.0) > 1e-9:
            raise ValueError(f"局势读数的四类之和必须是 1：{values!r}")

    def p(self, side: str) -> float:
        """按名字读一类（``lord`` / ``rebel`` / ``renegade`` / ``draw``）。"""
        try:
            index = POSITION_SIDES.index(side)
        except ValueError as error:
            raise ValueError(f"未知的局势类：{side!r}（可选 {POSITION_SIDES}）") from error
        return self.probabilities[index]

    def as_dict(self) -> dict[str, float]:
        """``{类名: 概率}``（报表 / JSON 用；顺序固定）。"""
        return dict(zip(POSITION_SIDES, self.probabilities, strict=True))


def read(view: PlayerView) -> PositionReading:
    """**读一次局势**：公开视角 → 四类结局概率。

    这是本模块的唯一读出口；纯函数，不改变 ``view``、不依赖调用顺序。
    """
    sides = _side_marginals(view)
    alive = [player for player in view.players if player.alive]
    if not alive:
        return PositionReading((0.0, 0.0, 0.0, 1.0))

    strengths = _strengths(view, sides)
    nonzero = [index for index in (LORD, REBEL, RENEGADE) if strengths[index] > 0.0]
    if len(nonzero) == 1:
        values = [0.0, 0.0, 0.0, 0.0]
        values[nonzero[0]] = 1.0
        return PositionReading(tuple(values))
    return _probabilities(strengths)


def _side_marginals(view: PlayerView) -> tuple[tuple[float, float, float], ...]:
    """每座位的 ``(P(主忠方), P(反贼), P(内奸))``（只对存活座位有值，死亡座位给 0）。"""
    marginals = identity_model.marginals(view)
    if marginals is None:
        marginals = _fallback_marginals(view)
    result: list[tuple[float, float, float]] = []
    for player, row in zip(view.players, marginals, strict=True):
        if not player.alive:
            result.append((0.0, 0.0, 0.0))
            continue
        by_identity = {
            Identity.LORD: row[0],
            Identity.LOYALIST: row[1],
            Identity.REBEL: row[2],
            Identity.RENEGADE: row[3],
        }
        result.append(
            (
                by_identity[Identity.LORD] + by_identity[Identity.LOYALIST],
                by_identity[Identity.REBEL],
                by_identity[Identity.RENEGADE],
            )
        )
    return tuple(result)


def _fallback_marginals(view: PlayerView) -> tuple[tuple[float, float, float, float], ...]:
    """身份后验算不出时的**公开身份摊派**（不猜：未知座位按剩余配比均分）。

    与 :func:`sgs.agents.identity.marginals` 的均匀路径同一条口径，但只读 ``player.identity``
    与 ``IDENTITY_ROSTERS``，不依赖"本人身份已知"——构造用例 / PvE 视图也能读出数。
    """
    roster = list(IDENTITY_ROSTERS.get(len(view.players), ()))
    known = [player.identity for player in view.players if player.identity is not None]
    for identity in known:
        if identity in roster:
            roster.remove(identity)
    unknown_alive = sum(
        1 for player in view.players if player.alive and player.identity is None
    )
    if unknown_alive and roster:
        unknown = tuple(
            roster.count(identity) / len(roster)
            for identity in (
                Identity.LORD,
                Identity.LOYALIST,
                Identity.REBEL,
                Identity.RENEGADE,
            )
        )
    else:
        unknown = (0.0, 0.0, 0.0, 0.0)
    result: list[tuple[float, float, float, float]] = []
    for player in view.players:
        identity = player.identity
        if identity is None:
            result.append(unknown)
            continue
        result.append(
            tuple(1.0 if identity is candidate else 0.0 for candidate in (
                Identity.LORD,
                Identity.LOYALIST,
                Identity.REBEL,
                Identity.RENEGADE,
            ))
        )
    return tuple(result)


def _strengths(
    view: PlayerView, sides: tuple[tuple[float, float, float], ...]
) -> tuple[float, float, float, float]:
    """四类的"实力和"（draw 类不参与 strength，只在 softmax 里给底）。"""
    totals = [0.0, 0.0, 0.0]
    for player, (p_lord, p_rebel, p_renegade) in zip(view.players, sides, strict=True):
        if not player.alive:
            continue
        value = (
            _W_HP * (player.hp + player.armor)
            + _W_HAND * player.hand_count
            + _W_EQUIP * len(player.equip)
            + _W_ALIVE
        )
        totals[LORD] += p_lord * value
        totals[REBEL] += p_rebel * value
        totals[RENEGADE] += p_renegade * value
    return (totals[LORD], totals[REBEL], totals[RENEGADE], 0.0)


def _probabilities(strengths: tuple[float, float, float, float]) -> PositionReading:
    """``strength/T + bias`` 过 softmax；draw 用固定底，主 / 反 / 内按 strength 归一。

    平局不参与 strength：它的概率固定为 :data:`_DRAW_FLOOR`（再归一），否则"双方都很弱"
    会被误读成"平局概率高"，而身份局几乎没有平局。
    """
    logits = [
        strengths[LORD] / _TEMPERATURE + _BIAS[LORD],
        strengths[REBEL] / _TEMPERATURE + _BIAS[REBEL],
        strengths[RENEGADE] / _TEMPERATURE + _BIAS[RENEGADE],
    ]
    top = max(logits)
    exponents = [math.exp(value - top) for value in logits]
    total = sum(exponents)
    survival = 1.0 - _DRAW_FLOOR
    values = [exponent / total * survival for exponent in exponents]
    values.append(_DRAW_FLOOR)
    return PositionReading(tuple(values))
