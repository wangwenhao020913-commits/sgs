"""训练管线的**纯 Python** 辅助（`ai-train` 票 01；`tools/sgs_train/fit.py`）。

门禁只付小钱：**不 import numpy**（仓内 `.venv` 没有；真正的 Newton / 信任域拟合在训练
venv 里跑，读数写进票面 `## Answer`），也不碰 `.scratch/`。这里钉住四件事：

1. 结局 → pair 权重（胜 1.0 / 负 `lose_weight`）的映射；
2. 信任域投影（`ε = 0` = 关闭，不能把权重拉回锚）；
3. 按**局种子**切 train / valid（互补、不交、确定性）；
4. 样本读取（`query_seed` / `data_sha256`）与报告渲染（默认配置**不**加配置行——
   默认路径必须逐字节复现 08c 的落仓 `weights_data.py`）；
5. 票 03 的语义约束：D4 向量来自**共用**局面构造器、半空间投影可行 / 幂等、报表字段。
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

from sgs_train import fit, semantic_constraints  # noqa: E402


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
        "data_sha256": "a" * 64,
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
    assert "数据 sha256 = " + "a" * 64 in source
    # 只加一行 docstring；表本身的结构与默认渲染一致
    default_lines = fit.render_weights_data(_report()).splitlines()
    configured_lines = source.splitlines()
    assert len(configured_lines) == len(default_lines) + 1
    assert "LOSE_WEIGHT" not in source and "TRUST_REGION" not in source


# ---------------------------------------------------------------------------
# 票 03：D4 语义约束（选项 A）
# ---------------------------------------------------------------------------


D4_SAMPLE_KEYS = (
    "b|装备先手",
    "b|伤害牌底价",
    "b|目标数",
    "b|期望伤害",
    "b|敌我关系",
)


def test_d4_constraint_uses_the_shared_constructor_and_the_hand_rule():
    """约束向量 = 同一 D4 构造器的 B 特征差：装刀 4.1（含手写 +0.1）− 杀 4.0 = 0.1。"""
    constraint = semantic_constraints.d4_constraint(D4_SAMPLE_KEYS)
    assert constraint.name == "d4_equip_before_lethal_slash"
    assert constraint.margin == pytest.approx(semantic_constraints.D4_MARGIN)
    lookup = dict(zip(constraint.keys, constraint.coefficients, strict=True))
    assert lookup["b|装备先手"] == pytest.approx(4.1)
    assert lookup["b|伤害牌底价"] == pytest.approx(-3.0)
    assert lookup["b|目标数"] == pytest.approx(-1.0)
    assert lookup["b|期望伤害"] == 0.0
    assert lookup["b|敌我关系"] == 0.0
    # 锚点（w = 1）的 gap 就是手写 +0.1；约束在锚点可行。
    assert constraint.gap([1.0] * len(D4_SAMPLE_KEYS)) == pytest.approx(0.1)
    assert constraint.satisfied([1.0] * len(D4_SAMPLE_KEYS))


def test_d4_constraint_rejects_a_sample_key_space_without_the_d4_keys():
    """样本表缺 D4 的键 ⇒ 约束无法展开，响亮失败（不静默丢项）。"""
    with pytest.raises(SystemExit):
        semantic_constraints.d4_constraint(("b|装备先手", "b|伤害牌底价"))


def test_half_space_projection_is_bounded_feasible_and_idempotent():
    """半空间 × 盒子的投影：落在交集内、贴边可复现、边界上再投不变。"""
    constraint = semantic_constraints.LinearConstraint(
        name="synthetic", keys=("a", "b"), coefficients=(1.0, -1.0), margin=0.0,
        source="test",
    )
    lower = [0.95, 0.95]
    upper = [1.05, 1.05]
    values = [0.95, 1.05]  # gap = 0.95 − 1.05 = −0.1，越界但盒内可行
    projected = semantic_constraints.project_to_feasible(
        values, (constraint,), lower, upper
    )
    assert constraint.gap(projected) >= -1e-12
    assert all(lo - 1e-12 <= value <= hi + 1e-12 for value, lo, hi in zip(projected, lower, upper, strict=True))
    again = semantic_constraints.project_to_feasible(
        projected, (constraint,), lower, upper
    )
    assert again == pytest.approx(projected)


def test_report_block_records_margin_gap_and_integer_reading():
    constraint = semantic_constraints.LinearConstraint(
        name="d4", keys=("a",), coefficients=(1.0,), margin=0.02, source="test"
    )
    block = semantic_constraints.report_block(constraint, [1.02], [1.02])
    assert block["margin"] == pytest.approx(0.02)
    assert block["gap"] == pytest.approx(1.02)
    assert block["satisfied"] is True
    assert block["integer_gap"] == pytest.approx(1.02)
    assert block["integer_satisfied"] is True


def test_cli_has_no_constraint_by_default_and_accepts_d4():
    parser = fit._build_parser()
    default = parser.parse_args(["fit", "--samples", "x", "--lambda-b", "1"])
    assert default.constraint == []
    chosen = parser.parse_args(
        ["fit", "--samples", "x", "--lambda-b", "1", "--constraint", "d4"]
    )
    assert chosen.constraint == ["d4"]


def test_render_records_the_semantic_constraint_line():
    report = _report(lose_weight=0.5, trust_region=0.05, valid_mod=5)
    report["constraints"] = [
        {
            "name": "d4_equip_before_lethal_slash",
            "margin": 0.02,
            "gap": 0.0201,
            "satisfied": True,
            "integer_gap": 0.019,
            "integer_satisfied": True,
            "source": "test",
        }
    ]
    source = fit.render_weights_data(report)
    assert "语义约束：" in source
    assert "d4_equip_before_lethal_slash" in source
    assert "margin = 0.02" in source
    assert "整数量化满足 = True" in source
