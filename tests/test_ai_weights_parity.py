"""整数权重的 parity 与橡皮筋（`ai-view` 票 08b；spec §2.7 / 票面验收 1、2）。

两条判据都**不依赖仓外产物**（`.scratch/` 是 gitignore 的，门禁不能读它）：

1. **整数权重 vs 浮点参考**：仓里存的是 ``round(w × 1000)``。真正的浮点拟合值落在
   量化格 ``[q − 0.5, q + 0.5] / 1000`` 里，故"两个端点都不改决策"⇒ 格内任何浮点值都不改
   （权重连续变化时 argmax 是分段常值）。这比"和某一组浮点值比"更强，也不需要把浮点表
   复制进仓。
2. **橡皮筋是连续的**：同一把 ``FIT_LAMBDA`` 下，样本 5 条的键被按在锚附近，样本多的键
   自由移动。用一维闭式解钉住——不需要 numpy，也不需要重跑拟合。
"""

from __future__ import annotations

import pytest

from sgs.agents import features, weights
from sgs.agents import heuristic as heuristic_scoring
from sgs.agents.weights_data import FIT_LAMBDA, FIT_SAMPLES
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.engine.rng import ai_stream

PACK = load_content_pack(deck_profile="standard_108")
SEEDS: tuple[int, ...] = (7008, 7009)
"""两局足够：判据是"每条询问上 argmax 相同"，不是统计量。"""


def _argmax_set(scores: list[float]) -> frozenset[int]:
    best = max(scores)
    return frozenset(index for index, value in enumerate(scores) if value == best)


def _weight_of(key: str, offset: float) -> float:
    """键在量化格里的一个端点：拟合值 ``(q + offset) / 1000``，无拟合值仍是锚。"""
    raw = weights.SHARED_B.value(key)
    if raw is None:
        return weights.anchor(key)
    return (raw + offset) / weights.WEIGHT_SCALE


def test_integer_weights_keep_every_decision_of_the_quantization_cell():
    """整数量化**没有改变任何决策**：格的两个端点与生产读数给出同一个 argmax 集合。"""
    if not weights.has_fitted_b():
        pytest.skip("权重表还是空的（08a 落地态）；拟合后这条才有判据意义")

    checked = 0
    mismatches: list[str] = []
    original = heuristic_scoring.HeuristicPlayer.respond

    def capture(player, query):
        if len(query.legal_actions) >= 2:
            actions = list(query.legal_actions)
            frames = [features.frame(query, action) for action in actions]
            production = [
                sum(item * weights.weight(key) for key, item in frame.items)
                for frame in frames
            ]
            low = [
                sum(item * _weight_of(key, -0.5) for key, item in frame.items)
                for frame in frames
            ]
            high = [
                sum(item * _weight_of(key, 0.5) for key, item in frame.items)
                for frame in frames
            ]
            expected = _argmax_set(production)
            if _argmax_set(low) != expected or _argmax_set(high) != expected:
                mismatches.append(f"{query.kind}@{query.actor}#{query.seq}")
        return original(player, query)

    heuristic_scoring.HeuristicPlayer.respond = capture
    try:
        for seed in SEEDS:
            engine = Engine.new_game(
                seed=seed,
                rule_config=RuleConfig(player_count=5),
                pack=PACK,
                hero_pool=(),
            )
            engine.run(
                [
                    heuristic_scoring.HeuristicPlayer(ai_stream(seed, seat))
                    for seat in range(5)
                ]
            )
            checked += 1
    finally:
        heuristic_scoring.HeuristicPlayer.respond = original

    assert checked == len(SEEDS)
    assert not mismatches, "量化格端点改了决策：" + "；".join(mismatches[:5])


def _ridge_one_key(sample_scores: list[float], anchor: float, lam: float) -> float:
    """一维闭式解：``w = (Σ x·y + λa) / (Σ x² + λ)``（``x = 1`` 每个样本）。"""
    total = sum(sample_scores)
    return (total + lam * anchor) / (len(sample_scores) + lam)


def test_the_rubber_band_keeps_five_samples_near_the_anchor():
    """皮筋**连续**：5 条样本的键仍贴着锚；样本多时数据把键拉走（不是阈值开关）。"""
    if FIT_LAMBDA <= 0 or FIT_SAMPLES <= 0:
        pytest.skip("还没有拟合报告（FIT_LAMBDA / FIT_SAMPLES = 0）")

    anchor = 1.0
    scale = 3.5
    rare = _ridge_one_key([scale, -scale, scale, -scale, scale], anchor, FIT_LAMBDA)
    common = _ridge_one_key([scale] * 5000, anchor, FIT_LAMBDA)

    assert abs(rare - anchor) <= 0.1 * anchor, (
        f"5 条样本的键被拉到了 {rare:.3f}（锚 {anchor}）：皮筋没有连续性"
    )
    assert common > anchor * 1.1, f"5000 条样本的键仍被按在 {common:.3f}：λ 盖过了数据"


def test_the_shipped_table_is_integer_millis():
    """仓里的表是**整数刻度**（逐字节可复现的前提），且没有越界的离谱值。"""
    for key, value in weights.SHARED_B.rows:
        assert isinstance(value, int) and not isinstance(value, bool), key
        assert abs(value) <= 10_000, f"{key} = {value} 超出合理量级（±10 倍锚）"
