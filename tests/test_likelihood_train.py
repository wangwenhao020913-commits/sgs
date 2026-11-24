"""似然表估计工具的**纯函数**（`ai-view` 票 07；`tools/sgs_train/likelihood.py`）。

门禁只付小钱：不跑任何对局，只核"计数 → 概率"的估计口径与源码渲染的确定性。真实采样
（多版本混合对局）是人工 / 受管后台任务，读数写进票面 `## Answer`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from sgs.agents import identity  # noqa: E402
from sgs_train.likelihood import Counts, estimate, render_source  # noqa: E402


def _toy_counts() -> Counts:
    """两组观察者 × 发起身份 × 行为类的少量样本；含空格子（测平滑 / 均分）。"""
    counts = Counts()
    counts.add("rebel", "lord", "harm_lord", "first")
    counts.add("rebel", "lord", "harm_lord", "first")
    counts.add("loyalist", "lord", "harm_lord", "first")
    counts.add("rebel", "lord", "help_lord", "first")
    counts.add("loyalist", "lord", "pass_dying_lord", "first")
    counts.add("rebel", "loyalist", "harm_me", "first")
    counts.add_pressure("rebel", "lord", "pressure_lord", 2)
    counts.add_pressure("rebel", "lord", "pressure_other", 1)
    counts.add_pressure("loyalist", "lord", "pressure_me", 1)
    return counts


def test_estimate_is_a_conditional_distribution_with_positive_smoothing():
    """每个 (发起, 观察者, 先手方) 的九类求和为 1；空格子也 > 0（平滑而非零概率）。"""
    estimated = estimate(_toy_counts())
    order = identity.IDENTITY_ORDER
    for context in identity.CONTEXTS:
        for viewer in order:
            for actor in order:
                total = sum(
                    estimated.table[(behavior, context, viewer.value)][
                        identity.IDENTITY_INDEX[actor]
                    ]
                    for behavior in identity.BEHAVIOR_CLASSES
                )
                assert total == pytest.approx(1.0, abs=1e-12)
                for behavior in identity.BEHAVIOR_CLASSES:
                    value = estimated.table[(behavior, context, viewer.value)][
                        identity.IDENTITY_INDEX[actor]
                    ]
                    assert 0.0 < value < 1.0
    for viewer in order:
        for actor in order:
            total = sum(
                estimated.pressure_table[(behavior, viewer.value)][
                    identity.IDENTITY_INDEX[actor]
                ]
                for behavior in identity.PRESSURE_CLASSES
            )
            assert total == pytest.approx(1.0, abs=1e-12)


def test_the_estimator_reads_the_counts_in_the_expected_direction():
    """反贼打主公的计数更多 ⇒ 同一格上 rebel 的 harm_lord 概率高于 loyalist。"""
    estimated = estimate(_toy_counts())
    row = estimated.table[("harm_lord", "first", "lord")]
    assert row[identity.IDENTITY_INDEX["rebel"]] > row[
        identity.IDENTITY_INDEX["loyalist"]
    ]


def test_render_source_is_deterministic_and_covers_every_row():
    """同一份估计渲染两次逐字节相同；两张表的每个键都出现在源码里。"""
    estimated = estimate(_toy_counts())
    first = render_source(estimated)
    second = render_source(estimate(_toy_counts()))

    assert first == second
    for context in identity.CONTEXTS:
        for viewer in identity.IDENTITY_ORDER:
            for behavior in identity.BEHAVIOR_CLASSES:
                assert f'("{behavior}", "{context}", "{viewer.value}")' in first
    for behavior in identity.PRESSURE_CLASSES:
        for viewer in identity.IDENTITY_ORDER:
            assert f'("{behavior}", "{viewer.value}")' in first
