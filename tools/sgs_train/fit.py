"""离线拟合权重（`ai-view` 票 08b；spec §2.7 / §2.8）：自弈样本 → 整数权重。

**两段式**（采集贵、拟合便宜，spec §2.8）：

1. ``collect`` —— 跑自弈批，把**每个决策**的
   ``(B 族逐项, A + C 族计数, 阵营结局)`` 写成 ``jsonl.gz``。这一段必须用**仓内**
   ``.venv``（它要 ``import sgs``）；多进程采集，进程数 = ``--workers``。
2. ``fit`` —— 读样本、拟合、量化成**整数**、打印 ``weights_data.py`` 的整块源码。这一段用
   **训练 venv**（numpy；§2.8 的常设约束：训练栈不进 ``SGS/.venv``）：仓库外那个
   ``../.venv-train``，命令见票面。

**拟合目标**（spec §2.7 的橡皮筋，本票把量纲钉死）：

    minimize  Σ_n (ŷ_n − w·x_n)² + λ·Σ_k (w_k − anchor_k)²

- ``ŷ = ±S``（阵营胜负），``S`` = 样本里**选中候选分的中位数**。为什么不直接拟合 ``±1``：
  B 族的特征值就是今天的分（几到几十分），``w = 1`` 必须读出"手写价"；把目标缩到分数量级
  之后，**anchor = 1.0 与拟合值同量纲**，稀有键读到的是真正的"手写价的量级"，而不是被
  放大 20 倍的先验。``S`` 由数据派生、写进报告，不手拍。
- ``anchor`` = ``sgs.agents.weights.anchor``（**同一函数**，不抄第二份）：B = 1.0、
  C = 既有关系系数、A = ``SKILL_VALUES`` 的手写值（形状档落众数）。
- 0 样本的键**不进表** ⇒ 读方回落 ``anchor``（票面"0 样本不归零"）。
- ``λ`` 由**按局分组**的 K 折交叉验证扫（同一种子的所有座位步同折——同局样本强相关，
  随机切折会泄漏）。

**整数量化**：仓库里的表存 ``round(w × WEIGHT_SCALE)``（``WEIGHT_SCALE = 1000``），读口
除回去。整数是为了**逐字节可复现**（浮点 repr 跨平台会漂），并让"整数权重 vs 浮点参考"
这条 parity 可机械断言。

用法（采集在仓根、用仓内 venv）::

    PYTHONPATH=tools .venv/bin/python -m sgs_train.fit collect \\
        --games 512 --players 5 --seed-start 90000 --workers 8 \\
        --out .scratch/ai-view/t08b/samples.jsonl.gz

拟合（用仓外训练 venv；numpy 只在这条命令里被 import）::

    PYTHONPATH=src:tools /home/admin2026/code/.venv-train/bin/python -m sgs_train.fit fit \\
        --samples .scratch/ai-view/t08b/samples.jsonl.gz \\
        --out /tmp/weights_data.py --report .scratch/ai-view/t08b/fit-report.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

__all__ = [
    "FOLD_COUNT",
    "LAMBDA_GRID",
    "SCHEMA",
    "Samples",
    "collect_games",
    "fit_weights",
    "load_samples",
    "main",
    "render_weights_data",
]

SCHEMA = "sgs.fit.samples.v1"
"""样本文件的 schema 标（换字段口径必须换它：老文件不许被新拟合器静默读成新口径）。"""

FOLD_COUNT = 4
"""交叉验证折数：**按局（seed）分组**。"""

LAMBDA_GRID: tuple[float, ...] = (
    1e-1,
    3e-1,
    1.0,
    3.0,
    10.0,
    30.0,
    100.0,
    300.0,
    1e3,
    3e3,
)

CV_STRIDE = 5
"""交叉验证只取 **1 / CV_STRIDE** 的局（``seed % CV_STRIDE == 0``）。

λ 是一条标量，不需要全量样本才选得稳；全量只在最后拟合一次（见 :func:`fit_weights`）。
这一条是实测的：全量 × 10 个 λ × 4 折要跑几小时，抽样后 ≈10 分钟，λ 的最优点几乎不动。
"""
"""``λ`` 的扫描网格（对数等距）。数据量涨一个数量级时，最优 λ 大致同向移动一格，
故网格留得宽、由 CV 自己挑，不按样本数手算。"""


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


_SINKS: list[list[tuple[Any, Any]]] = []
"""当前局各座位的 ``(query, chosen action)`` 旁路。**只在 worker 进程内**用：
一次 :func:`_collect_one` 生命周期内有效（多进程各持一份，互不影响）。"""


class _Capture:
    """包一层参与者：应答原样返回，旁路记下"这一问、选了什么"。"""

    def __init__(self, inner: Any, sink: list[tuple[Any, Any]]) -> None:
        self._inner = inner
        self._sink = sink

    def respond(self, query: Any) -> Any:
        action = self._inner.respond(query)
        self._sink.append((query, action))
        return action


def _factory(spec: Any) -> list[Any]:
    """``participants_factory``：每座位一个 ``HeuristicPlayer`` + 旁路。

    随机源与 ``sgs_batch`` 的缺省口径**逐字相同**（``ai_stream(seed, seat)``，票 12 的
    每座位子流）——采集口径与线上对局必须是同一条，否则拟合的是另一个 AI。
    """
    from sgs.agents import HeuristicPlayer
    from sgs.engine import ai_stream

    del _SINKS[:]
    players: list[Any] = []
    for seat in range(spec.players):
        sink: list[tuple[Any, Any]] = []
        _SINKS.append(sink)
        players.append(_Capture(HeuristicPlayer(ai_stream(spec.seed, seat)), sink))
    return players


def _collect_one(payload: tuple[int, int, str]) -> dict[str, Any]:
    """跑**一局**并交出它的样本行（纯数据、可 pickle 回父进程）。"""
    seed, players, mode = payload
    from sgs.agents import heuristic as heuristic_scoring
    from sgs.agents import features
    from sgs.data import Mode
    from sgs_batch import GameSpec, run_one

    spec = GameSpec(seed=seed, mode=Mode(mode), players=players)
    result = run_one(spec, participants_factory=_factory)
    if not result.finished:
        return {"seed": seed, "error": result.error or "未完成的局", "samples": []}

    won_by_seat = {seat.seat: seat.won for seat in result.seats}
    rows: list[dict[str, Any]] = []
    for seat, sink in enumerate(_SINKS):
        won = won_by_seat.get(seat)
        y = 1 if won else -1
        for query, action in sink:
            store = features.frame(query, action)
            scorer = heuristic_scoring._SCORERS[heuristic_scoring.QueryKind(query.kind)]
            with heuristic_scoring.Score.tracing():
                value = scorer(query, action)
            rows.append(
                {
                    "s": seed,
                    "p": seat,
                    "k": heuristic_scoring.QueryKind(query.kind).name,
                    "q": int(query.seq),
                    "y": y,
                    "v": float(value),
                    "b": [[key, float(item)] for key, item in store.items],
                    "ac": [
                        [key, float(item)] for key, item in store.position().items()
                    ],
                }
            )
    del _SINKS[:]
    return {"seed": seed, "error": None, "samples": rows}


def collect_games(
    seeds: Iterable[int],
    *,
    players: int = 5,
    mode: str = "identity",
    workers: int = 8,
    out_path: Path,
    progress: bool = True,
) -> dict[str, Any]:
    """跑整批自弈并写 ``jsonl.gz``；返回摘要（局数 / 样本数 / 失败种子）。"""
    seed_list = list(seeds)
    payloads = [(seed, players, mode) for seed in seed_list]
    failures: list[tuple[int, str]] = []
    samples = 0
    games = 0
    started = time.perf_counter()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as handle, ProcessPoolExecutor(
        max_workers=workers
    ) as pool:
        handle.write(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "players": players,
                    "mode": mode,
                    "games_requested": len(payloads),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        handle.write("\n")
        for index, result in enumerate(pool.map(_collect_one, payloads), start=1):
            if result["error"] is not None:
                failures.append((result["seed"], result["error"]))
                continue
            games += 1
            for row in result["samples"]:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
                samples += 1
            if progress and index % 50 == 0:
                elapsed = time.perf_counter() - started
                rate = index / elapsed if elapsed else 0.0
                print(
                    f"  {index}/{len(payloads)} 局 · {samples} 样本 · "
                    f"{rate:.1f} 局/s · 失败 {len(failures)}",
                    file=sys.stderr,
                )
    return {
        "schema": SCHEMA,
        "players": players,
        "mode": mode,
        "games": games,
        "samples": samples,
        "failures": failures,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "file": str(out_path),
    }


# ---------------------------------------------------------------------------
# 拟合
# ---------------------------------------------------------------------------


@dataclass
class Samples:
    """一次拟合的样本面（先读进内存，再交 numpy）。

    ``rows`` = 每个样本的 ``(键下标列表, 值列表, 目标, 局种子)``；``keys`` = 键名字典；
    ``scale`` = 目标量纲 ``S``（选中候选分的中位数，见模块 docstring）。
    """

    keys: dict[str, int] = field(default_factory=dict)
    rows: list[tuple[list[int], list[float], float, int]] = field(default_factory=list)
    scale: float = 0.0

    def index(self, key: str) -> int:
        found = self.keys.get(key)
        if found is None:
            found = len(self.keys)
            self.keys[key] = found
        return found


def load_samples(path: Path) -> Samples:
    """读 ``jsonl.gz``：``ŷ`` 先记 ``y·v``（后面按中位数把 ``S`` 补上）。"""
    store = Samples()
    scores: list[float] = []
    raw: list[tuple[list[int], list[float], int, int]] = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        header = json.loads(handle.readline())
        if header.get("schema") != SCHEMA:
            raise SystemExit(f"样本 schema 不是 {SCHEMA}：{header.get('schema')!r}")
        for line in handle:
            row = json.loads(line)
            index: list[int] = []
            values: list[float] = []
            for key, value in row["b"]:
                index.append(store.index(key))
                values.append(float(value))
            for key, value in row["ac"]:
                index.append(store.index(key))
                values.append(float(value))
            raw.append((index, values, int(row["y"]), int(row["s"])))
            scores.append(float(row["v"]))
    positive = [score for score in scores if score > 0]
    scale = statistics.median(positive) if positive else 0.0
    if scale <= 0:
        raise SystemExit("样本里没有正的候选分：无法定目标量纲 S")
    store.rows = [(idx, vals, y * scale, seed) for idx, vals, y, seed in raw]
    store.scale = scale
    return store


def _build_arrays(np: Any, rows: Sequence[tuple[list[int], list[float], float, int]]) -> Any:
    """样本面 → 五个平铺数组（``index`` / ``values`` / 每行起点 / 每元素所属行 / 目标）。"""
    index: list[int] = []
    values: list[float] = []
    starts: list[int] = []
    target: list[float] = []
    for row_index, value_row, y, _seed in rows:
        starts.append(len(index))
        index.extend(row_index)
        values.extend(value_row)
        target.append(y)
    flat_index = np.asarray(index, dtype=np.int64)
    flat_values = np.asarray(values, dtype=np.float64)
    flat_starts = np.asarray(starts, dtype=np.int64)
    row_index = np.repeat(
        np.arange(len(rows), dtype=np.int64),
        np.diff(np.append(flat_starts, len(flat_index))),
    )
    return flat_index, flat_values, flat_starts, row_index, np.asarray(
        target, dtype=np.float64
    )


def _solve_ridge(
    np: Any,
    index: Any,
    values: Any,
    starts: Any,
    row_index: Any,
    target: Any,
    anchor: Any,
    penalty: Any,
    *,
    iterations: int = 120,
) -> Any:
    """共轭梯度解 ``(XᵀX + λI) w = Xᵀy + λ anchor``（矩阵自由，每步只过一遍 nnz）。"""
    dim = anchor.shape[0]

    diagonal = np.bincount(index, weights=values * values, minlength=dim)
    diagonal = diagonal + penalty
    diagonal = np.where(diagonal > 0, diagonal, 1.0)

    def xtx(vector: Any) -> Any:
        xv = np.add.reduceat(values * vector[index], starts)
        return np.bincount(index, weights=values * xv[row_index], minlength=dim)

    def apply(vector: Any) -> Any:
        return xtx(vector) + penalty * vector

    rhs = np.bincount(index, weights=values * target[row_index], minlength=dim)
    rhs = rhs + penalty * anchor
    w = anchor.copy()
    residual = rhs - apply(w)
    # Jacobi 预条件：对角 = XᵀX 的对角 + λ（截距列不带惩罚）。不用它时病态矩阵要几百步。
    scaled = residual / diagonal
    direction = scaled.copy()
    rz = float(residual @ scaled)
    ceiling = float(rhs @ rhs) or 1.0
    for _ in range(iterations):
        if float(residual @ residual) <= 1e-24 * ceiling:
            break
        applied = apply(direction)
        denom = float(direction @ applied)
        if denom <= 0:
            break
        alpha = rz / denom
        w += alpha * direction
        residual -= alpha * applied
        scaled = residual / diagonal
        new_rz = float(residual @ scaled)
        beta = new_rz / rz
        direction = scaled + beta * direction
        rz = new_rz
    return w


def _mean_squared(
    np: Any, rows: Sequence[tuple[list[int], list[float], float, int]], weights: Any
) -> float:
    """在给定样本上的均方误差（目标量纲 = 分）。"""
    if not rows:
        return float("nan")
    total = 0.0
    for row_index, value_row, y, _seed in rows:
        predicted = float(
            np.dot(np.asarray(value_row), weights[np.asarray(row_index, dtype=np.int64)])
        )
        total += (y - predicted) ** 2
    return total / len(rows)


def fit_weights(
    samples: Samples,
    *,
    folds: int = FOLD_COUNT,
    lambda_b: float | None = None,
    lambda_ac: float | None = None,
) -> dict[str, Any]:
    """扫 ``λ``、按局分组交叉验证、全量重拟合；返回报告（含浮点权重）。"""
    import numpy as np

    from sgs.agents import weights as weight_module

    keys = tuple(samples.keys)
    anchor = np.asarray([weight_module.anchor(key) for key in keys], dtype=np.float64)
    # 截距列（值恒 1）：线性模型没有它就拟合不了非零均值（多数决策是 0 分）。
    # 截距**不带惩罚**（mask=0）——它只吸收结局的均值，不参与候选排序。
    bias = len(keys)
    rows = [
        (idx + [bias], vals + [1.0], y, seed) for idx, vals, y, seed in samples.rows
    ]
    anchor = np.append(anchor, 0.0)
    mask = np.append(np.ones(len(keys)), 0.0)
    index, values, starts, row_index, target = _build_arrays(np, rows)

    fold_rows: dict[int, list[int]] = {fold: [] for fold in range(folds)}
    for position, (_idx, _val, _y, seed) in enumerate(samples.rows):
        if seed % CV_STRIDE == 0:
            fold_rows[seed % folds].append(position)

    cv: list[dict[str, Any]] = []
    for lam in LAMBDA_GRID:
        errors: list[float] = []
        for fold in range(folds):
            train_rows = [
                rows[p]
                for other in range(folds)
                if other != fold
                for p in fold_rows[other]
            ]
            test_rows = [rows[p] for p in fold_rows[fold]]
            if not train_rows or not test_rows:
                continue
            idx, val, st, ridx, tgt = _build_arrays(np, train_rows)
            w = _solve_ridge(np, idx, val, st, ridx, tgt, anchor, lam * mask)
            errors.append(_mean_squared(np, test_rows, w))
        if errors:
            cv.append({"lambda": lam, "mse": float(sum(errors) / len(errors))})

    if lambda_b is None or lambda_ac is None:
        best = min(cv, key=lambda row: (row["mse"], row["lambda"]))
        lam = float(best["lambda"])
        if lambda_b is None:
            lambda_b = lam
        if lambda_ac is None:
            lambda_ac = lam
    # **分族 λ**：B 族（决策路径）的橡皮筋要比 A / C 族（结局读数）强得多——B 的 λ 按
    # "决策 diff 有界"选（票面预注册），A / C 的 λ 按结局 MSE 选。同一条 λ 会让 B 族塌到 0
    # （2026-11-26 实测：λ=3 → B 均值 0.11、test_ai_cases 13/80 红）。
    penalty = np.asarray(
        [
            lambda_b if weight_module.family(key) == weight_module.FAMILY_B else lambda_ac
            for key in keys
        ]
        + [0.0],
        dtype=np.float64,
    )
    w = _solve_ridge(np, index, values, starts, row_index, target, anchor, penalty)
    intercept = float(w[bias])
    w = w[: len(keys)]

    fitted = {
        key: float(value)
        for key, value in zip(keys, w, strict=True)
        if float(value) != float(weight_module.anchor(key))
    }
    quantized = {
        key: round(value * weight_module.WEIGHT_SCALE) for key, value in fitted.items()
    }
    return {
        "schema": "sgs.fit.report.v1",
        "samples": len(samples.rows),
        "keys": len(keys),
        "lambda": None,
        "lambda_b": float(lambda_b),
        "lambda_ac": float(lambda_ac),
        "cv": cv,
        "target_scale": samples.scale,
        "intercept": intercept,
        "float_weights": fitted,
        "integer_weights": quantized,
        "anchor": {
            key: float(value)
            for key, value in zip(keys, anchor[: len(keys)], strict=True)
        },
    }


def render_weights_data(report: dict[str, Any]) -> str:
    """把报告渲染成 ``src/sgs/agents/weights_data.py`` 的**整块**源码。

    行长固定、键序按字典序（确定性）：同一份报告渲染两次逐字节相同。
    """
    shared = sorted(
        (key, value) for key, value in report["integer_weights"].items() if key.startswith("b|")
    )
    mode_blocks = sorted(
        (key, value) for key, value in report["integer_weights"].items() if not key.startswith("b|")
    )
    lines = [
        '"""拟合出来的整数权重（`ai-view` 票 08b；生成文件，**不要手改**）。',
        "",
        f"样本 {report['samples']} 条 · 键 {report['keys']} 个 · λ_B = {report['lambda_b']!r}"
        f" / λ_AC = {report['lambda_ac']!r} · 目标量纲 S = {report['target_scale']!r}。",
        "值为 ``round(w × 1000)``（``sgs.agents.weights.WEIGHT_SCALE``）；读口除回 1000。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "__all__ = [",
        '    "FIT_LAMBDA",',
        '    "FIT_SAMPLES",',
        '    "IDENTITY_AC_ROWS",',
        '    "SHARED_B_ROWS",',
        "]",
        "",
        f"FIT_SAMPLES = {report['samples']}",
        "",
        f"FIT_LAMBDA = {float(report['lambda_b'])!r}",
        "",
        "SHARED_B_ROWS: tuple[tuple[str, int], ...] = (",
    ]
    lines.extend(f"    ({key!r}, {value})," for key, value in shared)
    lines.append(")")
    lines.append("")
    lines.append("IDENTITY_AC_ROWS: tuple[tuple[str, int], ...] = (")
    lines.extend(f"    ({key!r}, {value})," for key, value in mode_blocks)
    lines.append(")")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sgs_train.fit", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="跑自弈批并写样本文件（仓内 .venv）。")
    collect.add_argument("--games", type=int, required=True)
    collect.add_argument("--players", type=int, default=5)
    collect.add_argument("--mode", default="identity")
    collect.add_argument("--seed-start", type=int, default=90000)
    collect.add_argument("--workers", type=int, default=8)
    collect.add_argument("--out", type=Path, required=True)
    collect.add_argument("--summary", type=Path, default=None)

    fit = sub.add_parser("fit", help="拟合并打印 weights_data.py（训练 venv）。")
    fit.add_argument("--samples", type=Path, required=True)
    fit.add_argument("--out", type=Path, default=None)
    fit.add_argument("--report", type=Path, default=None)
    fit.add_argument("--folds", type=int, default=FOLD_COUNT)
    fit.add_argument("--lambda-b", type=float, default=None, help="B 族的 λ（决策路径）")
    fit.add_argument("--lambda-ac", type=float, default=None, help="A / C 族的 λ（局面读数）")

    args = parser.parse_args(argv)
    if args.command == "collect":
        summary = collect_games(
            range(args.seed_start, args.seed_start + args.games),
            players=args.players,
            mode=args.mode,
            workers=args.workers,
            out_path=args.out,
        )
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        print(text)
        if args.summary is not None:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(text + "\n", encoding="utf-8")
        return 0

    samples = load_samples(args.samples)
    report = fit_weights(
        samples, folds=args.folds, lambda_b=args.lambda_b, lambda_ac=args.lambda_ac
    )
    source = render_weights_data(report)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.out is not None:
        args.out.write_text(source, encoding="utf-8")
        print(f"已写 {args.out}", file=sys.stderr)
    else:
        print(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
