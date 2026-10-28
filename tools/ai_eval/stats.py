"""统计纯函数（票 13；spec §2.3 / §2.5）：配对差、bootstrap CI、噪声底、分身份聚合。

全部**确定性**：bootstrap 用固定种子（:data:`BOOTSTRAP_SEED`），同输入恒得同区间——
评估结果要能事后复算，不能"这次 CI 窄一点"就改判。舍入只发生在 :meth:`to_dict` 的
输出边界（4 位小数），判定用未舍入值。

口径（spec §2.5 实测）：

- **配对差** = 同一副牌里"候选臂赢 − 基线臂赢"（取值 −1 / 0 / 1 的均值）；
- **噪声底 δ** = ``1.96 · sqrt(2 · 0.25 · 0.408 / N)``：
  ``0.408`` 是实测"逐副配对差方差只有独立口径的 0.408"（2.45× 效率），
  ``0.25`` 是伯努利最坏方差（不拿观测胜率当常数，δ 因此与读数无关）；
  N = 768 ⇒ **0.0319（约 3pp）**，N = 128 ⇒ 0.078；
- **分身份**：按焦点座位的身份（lord / loyalist / rebel / renegade）分组，各报两臂胜率
  与配对差。1:1:2:1 凑批后整体胜率是四组的加权混合，"只报总数会骗人"（spec §2.5）。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .errors import AiEvalSpecError
from .seeds import IDENTITY_ORDER

__all__ = [
    "BOOTSTRAP_SEED",
    "PAIRED_VARIANCE_FACTOR",
    "PairedComparison",
    "bootstrap_mean_ci",
    "by_identity",
    "compare_pairs",
    "noise_floor",
    "paired_diff_ci",
]

BOOTSTRAP_RESAMPLES = 2000
"""bootstrap 重采样次数（默认；测试传更小的值换取速度，不影响生产口径）。"""

BOOTSTRAP_SEED = 20261028
"""bootstrap 的固定种子：同输入恒得同区间（评估结果可事后复算）。"""

PAIRED_VARIANCE_FACTOR = 0.408
"""spec §2.5 实测：逐副配对差的方差 / 独立口径的方差（ρ = 0.593 的等价物）。"""

Z95 = 1.96


def _percentile(sorted_values: Sequence[float], quantile: float) -> float:
    """线性插值分位数（单值 / 全同值退化为该值本身）。"""
    if not sorted_values:
        raise AiEvalSpecError("分位数需要非空样本")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = quantile * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """均值的 95% bootstrap 区间；固定种子 ⇒ 同输入同输出。"""
    if not values:
        raise AiEvalSpecError("bootstrap 需要非空样本")
    if isinstance(resamples, bool) or not isinstance(resamples, int) or resamples <= 0:
        raise AiEvalSpecError(f"resamples 必须是正整数，得到 {resamples!r}")
    rng = random.Random(seed)
    count = len(values)
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            total += values[rng.randrange(count)]
        means.append(total / count)
    means.sort()
    return (_percentile(means, 0.025), _percentile(means, 0.975))


def paired_diff_ci(
    differences: Sequence[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """逐副配对差（−1 / 0 / 1 或其均值）的 95% CI（spec §2.3 的 bootstrap 判据）。"""
    return bootstrap_mean_ci(differences, resamples=resamples, seed=seed)


def noise_floor(deals: int) -> float:
    """该 N 下的噪声底 δ（spec §2.3 条件②/③的容忍带）：N=768 ⇒ 约 3pp。"""
    if isinstance(deals, bool) or not isinstance(deals, int):
        raise AiEvalSpecError(f"deals 必须是整数，得到 {type(deals).__name__}")
    if deals <= 0:
        raise AiEvalSpecError(f"deals 必须是正整数，得到 {deals}")
    return round(Z95 * math.sqrt(2.0 * 0.25 * PAIRED_VARIANCE_FACTOR / deals), 4)


def _rate(wins: int, deals: int) -> float:
    return round(wins / deals, 4) if deals else 0.0


def by_identity(
    pairs: Sequence[tuple[str | None, bool, bool]],
) -> dict[str, dict[str, Any]]:
    """按焦点座位身份分组（只列**出现过**的身份，次序按主-忠-反-内）。"""
    grouped: dict[str, list[tuple[bool, bool]]] = {}
    for identity, candidate_won, baseline_won in pairs:
        if identity is None:
            continue
        grouped.setdefault(identity, []).append((candidate_won, baseline_won))
    breakdown: dict[str, dict[str, Any]] = {}
    for identity in IDENTITY_ORDER:
        if identity not in grouped:
            continue
        rows = grouped[identity]
        candidate_wins = sum(1 for candidate_won, _ in rows if candidate_won)
        baseline_wins = sum(1 for _, baseline_won in rows if baseline_won)
        breakdown[identity] = {
            "deals": len(rows),
            "candidate_wins": candidate_wins,
            "baseline_wins": baseline_wins,
            "candidate_rate": _rate(candidate_wins, len(rows)),
            "baseline_rate": _rate(baseline_wins, len(rows)),
            "diff": round((candidate_wins - baseline_wins) / len(rows), 4),
        }
    return breakdown


@dataclass(frozen=True)
class PairedComparison:
    """一次"候选 vs 基线"的换座配对读数（spec §2.3 条件②）。"""

    deals: int
    candidate_wins: int
    baseline_wins: int
    diff: float
    ci95: tuple[float, float]
    by_identity: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """§2.8 的 ``vs_baseline`` 对象（只数两臂都打完的副）。"""
        return {
            "win_rate_candidate": _rate(self.candidate_wins, self.deals),
            "win_rate_baseline": _rate(self.baseline_wins, self.deals),
            "diff": self.diff,
            "ci95": [self.ci95[0], self.ci95[1]],
            "by_identity": {key: dict(value) for key, value in self.by_identity.items()},
        }

    def anchor_view(self) -> dict[str, Any]:
        """§2.8 的 ``vs_anchor`` 对象：只报配对差与 CI（spec 未给绝对胜率位）。"""
        return {"diff": self.diff, "ci95": [self.ci95[0], self.ci95[1]]}


def compare_pairs(
    pairs: Sequence[tuple[str | None, bool | None, bool | None]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> PairedComparison:
    """把逐副的 ``(身份, 候选赢?, 基线赢?)`` 折成配对差 + CI + 分身份。

    ``None`` = 该臂没有有效结果（局崩了 / 没终局）：整副**退出分母**，绝不拿半副凑数。
    一份有效配对都没有时返回全零读数而不是抛错——批次的错误由 ``errors`` 单独计数。
    """
    matched = [
        (identity, bool(candidate_won), bool(baseline_won))
        for identity, candidate_won, baseline_won in pairs
        if candidate_won is not None and baseline_won is not None
    ]
    if not matched:
        return PairedComparison(
            deals=0,
            candidate_wins=0,
            baseline_wins=0,
            diff=0.0,
            ci95=(0.0, 0.0),
            by_identity={},
        )
    differences = [
        float(int(candidate_won) - int(baseline_won))
        for _, candidate_won, baseline_won in matched
    ]
    candidate_wins = sum(1 for _, candidate_won, _ in matched if candidate_won)
    baseline_wins = sum(1 for _, _, baseline_won in matched if baseline_won)
    low, high = paired_diff_ci(differences, resamples=resamples)
    return PairedComparison(
        deals=len(matched),
        candidate_wins=candidate_wins,
        baseline_wins=baseline_wins,
        diff=round(sum(differences) / len(differences), 4),
        ci95=(round(low, 4), round(high, 4)),
        by_identity=by_identity(matched),
    )
