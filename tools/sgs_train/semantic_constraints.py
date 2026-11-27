"""语义约束集（`ai-train` 票 03 选项 A）：把 D4 的先后顺序写成 fit 的线性不等式。

票 03 的裁定：D4「古锭刀先手」是**硬语义**——目标 2 体力 0 手时，先装【古锭刀】再补
【杀】是规则级的要求，不是手写 tie-break 可以随便翻掉的软偏好。故本模块把它写进拟合：

    w · (x_equip − x_slash) ≥ margin

- ``x`` 是 D4 询问里两个候选的 **B 族特征向量**，由
  :mod:`tests.support.positions` 的**同一个**局面构造器（``d4_query`` / ``d4_actions``）
  派生，不在训练脚本里手抄一份特征（train/serve skew 的头号来源）；
- 键面是**桶化键** ``b|<项名>@<token>``（`ai-train` 票 08b）：token 从
  ``features.bucket(query)`` 取（D4 局面 = 2 人 / PLAY_PHASE ⇒ ``a0p0``），键一律由
  ``weights.bucket_key(项名, token)`` 拼——训练侧不手写 ``@``、不手抄 token；
- 约束只接受**桶化样本键面**（v3 + ``--bucket-set alive,phase``）：缺桶键 / 拿到 v2 的基键
  都响亮失败，不静默退回"无桶"（那会让同一张票在两种键面上读出两种 gap）；
- 约束是**一条半空间**（先只有 D4；不扩大约束集），投影到 ``{w : a·w ≥ margin}`` 与信任域
  的交集：一条半空间 + 一个盒子的投影有闭式（盒子内再沿 ``a`` 找到刚好满足的 λ），
  实现走纯 Python（样本全键向量；容量表 245–492 维，单步 < 1e-4 s）；
- ``margin`` 取 **0.05（手写 +0.1 的一半，50 个整数量化格）**：它保住"严格先装"并给量化
  留 50 倍余量，又**不**要求拟合保留手写 +0.1 的全部间隙（ai-v13 在加权后只剩 0.0164；
  实测 0.1 在当前等式约束 Newton / 线搜索上不可行）。手写 ``_EQUIP_SETUP_MARGIN = 0.1``
  的价不改，约束只把**先后符号**钉住并留半格缓冲。

约束只在显式 ``--constraint d4`` 时生效；默认路径（无约束）仍逐字节复现 `ai-v13`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

D4_MARGIN = 0.05
"""D4 约束的 margin（加权后的 B 分差下界）：手写 +0.1 的一半，50 个整数量化格。

0.1 = 手写规则的全部间隙，实测在等式约束 Newton / 线搜索上不可行；0.05 是"保先后 + 量化
缓冲"的稳定中点（0.02 在 108 档的一条近似平局上触发量化格端点换 argmax 的数值毛刺，见
票 03 的 Answer）。整数表至少留 0.0488 的 gap，远高于 ``WEIGHT_SCALE = 1000`` 的舍入。
"""

_D4_ITEMS = (
    "装备先手",
    "伤害牌底价",
    "目标数",
)
"""D4 两个候选实际不同的 B **项名**（自检用；键面由 ``weights.bucket_key`` 加本次 token 拼出）。"""


@dataclass(frozen=True)
class LinearConstraint:
    """一条 ``a·w ≥ margin`` 的线性语义约束（键序与样本键序一致）。"""

    name: str
    keys: tuple[str, ...]
    coefficients: tuple[float, ...]
    margin: float
    source: str

    def gap(self, values: Sequence[float]) -> float:
        """``a·w``（``w`` 按 :attr:`keys` 的次序）；长度不符即炸，不静默截断。"""
        if len(values) != len(self.coefficients):
            raise SystemExit(
                f"语义约束 {self.name!r} 的权重长度 {len(values)} != {len(self.coefficients)}"
            )
        return sum(
            coefficient * float(value)
            for coefficient, value in zip(self.coefficients, values, strict=True)
        )

    def gap_mapping(self, weights: Mapping[str, float]) -> float:
        """按"键 → 权重"映射求 ``a·w``（报表 / 单测的读口）。"""
        return sum(
            coefficient * float(weights.get(key, 0.0))
            for key, coefficient in zip(self.keys, self.coefficients, strict=True)
        )

    def satisfied(self, values: Sequence[float], *, tolerance: float = 1e-12) -> bool:
        return self.gap(values) >= self.margin - tolerance


def build(name: str, sample_keys: Sequence[str]) -> LinearConstraint:
    """按名字造约束；目前只有 ``d4``。未知名字 / 缺键都响亮失败。"""
    if name != "d4":
        raise SystemExit(f"未知的语义约束 {name!r}（当前只登记 d4）")
    return d4_constraint(sample_keys)


def d4_constraint(sample_keys: Sequence[str]) -> LinearConstraint:
    """从**共用的** D4 局面构造器取两个候选的 B 特征差，展开到样本键序。

    特征差按**项名**算，展开前一律走 ``weights.bucket_key(项名, token)``：token 由同一个
    构造器的 ``features.bucket(query)`` 给出（票 08b；D4 = ``a0p0``），不手写 ``@``、不在
    这里另抄一份 token。样本键面必须是桶化键（v3 + ``--bucket-set alive,phase``）；拿到
    v2 的基键 / 缺任何一个 D4 桶键都 ``SystemExit``，不静默退回无桶读法。

    训练 venv（只有 numpy）不装 pytest；``tests.support`` 本身不 import pytest，故可以
    在训练进程里直接用同一份构造器。路径按 ``__file__`` 定位仓库根，不依赖 cwd。
    """
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from sgs.agents import features, weights  # 延迟 import：默认路径不碰 tests/support

    from tests.support.positions import d4_actions, d4_query

    query = d4_query()
    token = features.bucket(query)
    equipment, slash = d4_actions(query)
    # 与采集（sgs_train.fit._b_items）**同一个** trace 入口 / 同一个归并函数；这里保留
    # **项名**，因为桶键要从项名 + token 拼（不先拼基键再反解）。
    equip_items = features._group(features._scored_items(query, equipment))
    slash_items = features._group(features._scored_items(query, slash))
    # 键的拼法只有一处：weights.bucket_key（项名 / token 的合法性也在那里 fail-closed）。
    equip_keys = {
        weights.bucket_key(item, token): float(value) for item, value in equip_items.items()
    }
    slash_keys = {
        weights.bucket_key(item, token): float(value) for item, value in slash_items.items()
    }
    keys = tuple(sample_keys)
    unknown = sorted((set(equip_keys) | set(slash_keys)) - set(keys))
    if unknown:
        raise SystemExit(
            f"D4 特征键不在样本键里（约束无法展开）：{unknown[:5]}；"
            f"现在按桶 token {token!r} 拼键，样本键面必须是桶化键"
            "（--bucket-set alive,phase 的 b|<项名>@<token>），不是 v2 的基键"
        )
    coefficients = tuple(
        equip_keys.get(key, 0.0) - slash_keys.get(key, 0.0) for key in keys
    )
    actual_keys = {
        key for key, coefficient in zip(keys, coefficients, strict=True) if coefficient != 0.0
    }
    expected_keys = {weights.bucket_key(item, token) for item in _D4_ITEMS}
    if actual_keys != expected_keys:
        raise SystemExit(
            f"D4 的非零 B 键变了：{sorted(actual_keys)}（期望 {sorted(expected_keys)}，"
            f"token={token!r}）；打分器 / 构造器 / 桶函数漂移，先核对再改约束"
        )
    constraint = LinearConstraint(
        name="d4_equip_before_lethal_slash",
        keys=keys,
        coefficients=coefficients,
        margin=D4_MARGIN,
        source=(
            "ai-train 票 03 选项 A / 票 25 D4 / 票 08b 桶化"
            f"（tests/support/positions.py::d4_query，token={token}）"
        ),
    )
    anchor_gap = sum(coefficients)
    if anchor_gap < D4_MARGIN - 1e-12:
        raise SystemExit(
            f"D4 约束在锚点就不可行（gap={anchor_gap:.6f} < margin={D4_MARGIN}）："
            "手写规则的 +0.1 没有进 B 特征，构造器 / 打分器已经漂了"
        )
    return constraint


# ---------------------------------------------------------------------------
# 投影：盒子（信任域）× 半空间交集
# ---------------------------------------------------------------------------


def _clip(
    values: Sequence[float],
    lower: Sequence[float] | None,
    upper: Sequence[float] | None,
) -> list[float]:
    if lower is None or upper is None:
        return [float(value) for value in values]
    return [
        min(max(float(value), float(low)), float(high))
        for value, low, high in zip(values, lower, upper, strict=True)
    ]


def project(
    values: Sequence[float],
    constraint: LinearConstraint,
    lower: Sequence[float] | None = None,
    upper: Sequence[float] | None = None,
) -> list[float]:
    """把 ``values`` 投影到 ``{w : a·w ≥ margin} ∩ [lower, upper]``。

    一条半空间 + 盒子的投影有闭式：盒子内沿 ``a`` 走，``λ`` 增大时 ``a·clip(v+λa)``
    单调不减；二分找到 ``a·w = margin`` 的那个点。无盒子时直接闭式解。
    """
    vector = [float(value) for value in values]
    candidate = _clip(vector, lower, upper)
    if constraint.gap(candidate) >= constraint.margin:
        return candidate
    coefficients = constraint.coefficients
    norm = sum(coefficient * coefficient for coefficient in coefficients)
    if norm <= 0.0:
        raise SystemExit(f"语义约束 {constraint.name!r} 的系数全 0：无法投影")
    if lower is None or upper is None:
        step = (constraint.margin - constraint.gap(vector)) / norm
        return [
            value + step * coefficient
            for value, coefficient in zip(vector, coefficients, strict=True)
        ]

    def shifted(step: float) -> list[float]:
        return _clip(
            [
                value + step * coefficient
                for value, coefficient in zip(vector, coefficients, strict=True)
            ],
            lower,
            upper,
        )

    def gap_at(step: float) -> float:
        return constraint.gap(shifted(step))

    high = 1.0
    while gap_at(high) < constraint.margin:
        high *= 2.0
        if high > 1e15:
            raise SystemExit(f"语义约束 {constraint.name!r} 在信任域内不可行")
    low = 0.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if gap_at(middle) >= constraint.margin:
            high = middle
        else:
            low = middle
    return shifted(high)


def project_to_feasible(
    values: Sequence[float],
    constraints: Sequence[LinearConstraint],
    lower: Sequence[float] | None = None,
    upper: Sequence[float] | None = None,
) -> list[float]:
    """逐条投影到所有约束的交集（当前只有一条，故一次即精确）。

    多条时是交替投影的近似；约束集**刻意保持一条**（票 03「不扩大约束集」），多条要另票
    换成 QP，不在这里悄悄扩。
    """
    point = _clip(values, lower, upper)
    if not constraints:
        return point
    for _ in range(200):
        before = list(point)
        for constraint in constraints:
            point = project(point, constraint, lower, upper)
        if all(constraint.satisfied(point) for constraint in constraints):
            return point
        if max(abs(a - b) for a, b in zip(point, before, strict=True)) <= 1e-15:
            break
    return point


def report_block(
    constraint: LinearConstraint,
    values: Sequence[float],
    integer_values: Sequence[float] | None = None,
) -> dict[str, object]:
    """报表里的约束读数：名字 / margin / 拟合 gap / 是否满足（+ 整数量化后的 gap）。"""
    gap = constraint.gap(values)
    block: dict[str, object] = {
        "name": constraint.name,
        "margin": constraint.margin,
        "gap": gap,
        "satisfied": bool(gap >= constraint.margin - 1e-12),
        "source": constraint.source,
    }
    if integer_values is not None:
        integer_gap = constraint.gap(integer_values)
        block["integer_gap"] = integer_gap
        # 落仓的是整数表：测试只要求"严格先装"（> 0），不要求量化后仍留满 margin。
        block["integer_satisfied"] = bool(integer_gap > 0.0)
    return block


__all__ = [
    "D4_MARGIN",
    "LinearConstraint",
    "build",
    "d4_constraint",
    "project",
    "project_to_feasible",
    "report_block",
]
