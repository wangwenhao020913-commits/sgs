"""训练管线的**纯 Python** 辅助（`ai-train` 票 01；`tools/sgs_train/fit.py`）。

门禁只付小钱：**不 import numpy**（仓内 `.venv` 没有；真正的 Newton / 信任域拟合在训练
venv 里跑，读数写进票面 `## Answer`），也不碰 `.scratch/`。这里钉住四件事：

1. 结局 → pair 权重（胜 1.0 / 负 `lose_weight`）的映射；
2. 信任域投影（`ε = 0` = 关闭，不能把权重拉回锚）；
3. 按**局种子**切 train / valid（互补、不交、确定性）；
4. 样本读取（`query_seed` / `data_sha256`）与报告渲染（默认配置**不**加配置行——
   默认路径必须逐字节复现 08c 的落仓 `weights_data.py`）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from sgs_train import fit  # noqa: E402


# ---------------------------------------------------------------------------
# 结局权重
# ---------------------------------------------------------------------------


def test_pair_weights_map_winner_and_loser_to_the_preregistered_grid():
    """胜方恒 1.0；负方 = lose_weight（1.0 = 08c 等权，0.0 = 只用胜方）。"""
    assert fit.pair_weights_from_sign([1, -1], 1.0) == [1.0, 1.0]
    assert fit.pair_weights_from_sign([1, -1], 0.25) == [1.0, 0.25]
    assert fit.pair_weights_from_sign([1, -1], 0.5) == [1.0, 0.5]
    assert fit.pair_weights_from_sign([1, -1], 0.0) == [1.0, 0.0]
    # 样本 schema 只写 ±1；0 / 负数按负方处理（只钉容错方向，不新增语义）。
    assert fit.pair_weights_from_sign([0, -7], 0.25) == [0.25, 0.25]
    assert fit.pair_weights_from_sign([], 0.25) == []


# ---------------------------------------------------------------------------
# 信任域
# ---------------------------------------------------------------------------


def test_trust_region_projection_is_a_symmetric_clip_around_the_anchor():
    """投影 = 逐键夹到 ``[anchor − ε, anchor + ε]``；锚不一定是 1。"""
    anchors = [1.0, 1.0, 1.0]
    assert fit.project_weights_to_trust_region([0.9, 1.0, 1.1], anchors, 0.05) == [
        0.95,
        1.0,
        1.05,
    ]
    # 域内原样通过（不是"总被拉向锚"）
    assert fit.project_weights_to_trust_region([0.9, 1.1], anchors[:2], 0.2) == [
        0.9,
        1.1,
    ]
    assert fit.project_weights_to_trust_region([2.0], [3.0], 0.5) == [2.5]
    # 端点本身留在域内（≤ 而不是 <）
    assert fit.project_weights_to_trust_region([1.05], [1.0], 0.05) == [1.05]


def test_trust_region_zero_means_disabled_not_pinned_to_the_anchor():
    """``ε = 0`` = 关闭：08c 的默认路径不做任何投影（把权重拉回锚会改掉 ai-v13）。"""
    assert fit.project_weights_to_trust_region([0.9, 1.1], [1.0, 1.0], 0.0) == [
        0.9,
        1.1,
    ]
    assert fit.project_weights_to_trust_region([0.9], [1.0], -1.0) == [0.9]


def test_trust_region_projection_rejects_mismatched_key_counts():
    with pytest.raises(ValueError):
        fit.project_weights_to_trust_region([1.0], [1.0, 2.0], 0.1)


def test_default_knobs_are_the_08c_reproduction_values():
    assert fit.DEFAULT_LOSE_WEIGHT == 1.0
    assert fit.DEFAULT_TRUST_REGION == 0.0
    assert fit.DEFAULT_VALID_MOD == 0
    parser = fit._build_parser()
    args = parser.parse_args(["fit", "--samples", "x.jsonl.gz", "--lambda-b", "3e5"])
    assert (args.lose_weight, args.trust_region, args.valid_mod) == (1.0, 0.0, 0)


# ---------------------------------------------------------------------------
# 按局切分
# ---------------------------------------------------------------------------


def test_train_valid_split_is_by_game_seed_complementary_and_disjoint():
    seeds = [90000, 90001, 90002, 90003, 90004, 90005, 90010]
    train, valid = fit.split_train_valid_masks(seeds, 5)
    assert len(train) == len(valid) == len(seeds)
    # 互补 ⇒ 不交且不漏：每个询问恰好落在一侧
    assert all(left != right for left, right in zip(train, valid, strict=True))
    assert [seed for seed, flag in zip(seeds, valid, strict=True) if flag] == [
        90000,
        90005,
        90010,
    ]
    # 同输入同输出（确定性）
    assert (train, valid) == fit.split_train_valid_masks(seeds, 5)
    # 0 / 负数 = 不切分：全部进 train、valid 为空
    off_train, off_valid = fit.split_train_valid_masks(seeds, 0)
    assert all(off_train) and not any(off_valid)
    assert fit.split_train_valid_masks(seeds, -3) == (off_train, off_valid)


# ---------------------------------------------------------------------------
# 样本读取：seed 与文件摘要（不换 schema）
# ---------------------------------------------------------------------------


def _write_samples(path: Path, rows: list[dict], schema: str = fit.SCHEMA) -> Path:
    header = {
        "schema": schema,
        "players": 5,
        "mode": "identity",
        "alternatives": fit.ALTERNATIVES,
        "games_requested": len({row["s"] for row in rows}),
    }
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(header, separators=(",", ":")) + "\n")
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return path


def _row(
    seed: int, seat: int, seq: int, y: int, value: float, others: int
) -> dict:
    return {
        "s": seed,
        "p": seat,
        "k": "play",
        "q": seq,
        "y": y,
        "v": value,
        "c": [["b|chosen", 1.0]],
        "o": [[["b|other", float(index)]] for index in range(others)],
    }


def test_load_pair_samples_reads_the_game_seed_and_hashes_the_file(tmp_path):
    """``query_seed`` 从既有的 ``s`` 字段读（不换 schema）；``data_sha256`` = 文件字节摘要。"""
    path = _write_samples(
        tmp_path / "samples.jsonl.gz",
        [
            _row(90000, 0, 1, 1, 3.5, 2),
            _row(90000, 1, 2, -1, 4.5, 1),
            _row(90001, 0, 3, 1, 6.0, 3),
        ],
    )
    samples = fit.load_pair_samples(path)
    assert samples.queries == 3
    assert samples.games == 2
    assert list(samples.query_seed) == [90000, 90000, 90001]
    assert samples.pairs == 6
    assert samples.candidates == 9
    assert samples.scale == 4.5
    assert samples.data_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_pair_samples_rejects_a_foreign_schema(tmp_path):
    path = _write_samples(
        tmp_path / "samples.jsonl.gz",
        [_row(90000, 0, 1, 1, 3.5, 1)],
        schema="sgs.fit.samples.v1",
    )
    with pytest.raises(SystemExit):
        fit.load_pair_samples(path)


# ---------------------------------------------------------------------------
# 渲染：默认配置不得改字节
# ---------------------------------------------------------------------------


def _report(**overrides: object) -> dict:
    report = {
        "samples": 12,
        "pairs": 30,
        "keys": 2,
        "lambda_b": 1e4,
        "target_scale": 3.5,
        "integer_weights": {"b|a": 1010, "b|b": 990},
        "lose_weight": 1.0,
        "trust_region": 0.0,
        "valid_mod": 0,
    }
    report.update(overrides)
    return report


def test_render_keeps_the_default_docstring_byte_stable():
    """默认配置**不**写配置行：这是"默认路径逐字节复现 08c 落仓文件"的渲染面保证。"""
    source = fit.render_weights_data(_report())
    assert "配置：" not in source
    assert source.splitlines()[:6] == [
        '"""拟合出来的整数权重（`ai-view` 票 08c；生成文件，**不要手改**）。',
        "",
        "由 ``tools/sgs_train/fit.py`` 整块重写；每行 = ``(键, round(w × 1000))``。",
        "B 族 = 成对偏好拟合（票 08c）；A / C 族沿用 08b 第一轮的值（票 09 的输入）。",
        "样本 12 条询问 / 30 对 · 键 2 个 · λ_B = 10000.0 · 目标量纲 S = 3.5"
        "（行为克隆：选中动作恒被偏好）。",
        '"""',
    ]
    assert fit.render_weights_data(_report()) == source


def test_render_records_non_default_configuration():
    source = fit.render_weights_data(
        _report(lose_weight=0.25, trust_region=0.05, valid_mod=5)
    )
    assert "配置：" in source
    assert "lose_weight = 0.25" in source
    assert "trust_region = 0.05" in source
    assert "valid_mod = 5" in source
    # 只加一行 docstring；表本身的结构与默认渲染一致
    default_lines = fit.render_weights_data(_report()).splitlines()
    configured_lines = source.splitlines()
    assert len(configured_lines) == len(default_lines) + 1
    assert "LOSE_WEIGHT" not in source and "TRUST_REGION" not in source
