"""离线拟合权重（`ai-view` 票 08c；spec §2.7 / §2.8）：自弈样本 → 整数权重。

**两段式**（采集贵、拟合便宜，spec §2.8）：

1. ``collect`` —— 跑自弈批，把**每个询问**的
   ``(选中候选的 B 分项, 至多 K 个其它候选的 B 分项, 阵营结局)`` 写成 ``jsonl.gz``。
   这一段必须用**仓内** ``.venv``（它要 ``import sgs``）；多进程采集，进程数 = ``--workers``。
2. ``fit`` —— 读样本、拟合、量化成**整数**、打印 ``weights_data.py`` 的整块源码。这一段用
   **训练 venv**（numpy；§2.8 的常设约束：训练栈不进 ``SGS/.venv``）：仓库外那个
   ``../.venv-train``，命令见票面。

**拟合目标**（票 08c：成对偏好 / 行为克隆，替换 08b 的终局值回归）：

    minimize  Σ_pairs softplus(−S·(s_chosen − s_alt)) + λ_B·Σ(w − anchor)²

- 每一对是**同一次询问**里的"选中动作 vs 另一个候选"：**选中动作恒为被偏好的一方**
  （行为克隆），``S`` = 样本里**选中候选分的中位数**（08b 的同一口径）。``w = 1`` 必须读出
  "手写价"，故目标量纲取分数本身，而不是 ±1（理由见 08b 的模块 docstring）。
- **口径裁定**：票面第 2 条把 ``y = ±S``（结局）写在 softplus 的方向位，与第 1 句「结局只作
  **样本权重**」冲突；2026-11-26 用户裁定取**行为克隆**口径（票面 ``## Comments``）：结局不进
  方向位，本轮也不额外加权（等权 = 所选选项的读数；胜负加权留给后续票）。样本表里仍保留
  每局的 ``y = ±1``（采集口径不变），拟合只读 ``S``。
- **没有截距**——只约束候选间的**差**，故 anchor = 1.0 在差尺度上天然成立，不会被结局的均值
  压平（08b 值回归的根因）。
- 其余候选**按值域截断**：每条询问只留**手写分最高**的 ``K`` 个（``--alternatives``，
  缺省 8）。选中动作永远在样本里（它可能来自 AI 自己拼的复合候选，不在 ``legal_actions``
  里——那时它仍是第 0 号候选）。
- ``anchor`` = ``sgs.agents.weights.anchor``（**同一函数**，不抄第二份）：本票只拟合 B 族
  （锚 1.0）；A / C 族沿用 08b 第一轮的值，由 ``fit --ac-report`` 从那份报告里原样搬进
  渲染结果（票 09 的输入，与决策无关）。
- 0 样本的键**不进表** ⇒ 读方回落 ``anchor``（票面"0 样本不归零"）。

**整数量化**：仓库里的表存 ``round(w × WEIGHT_SCALE)``（``WEIGHT_SCALE = 1000``），读口
除回去。整数是为了**逐字节可复现**（浮点 repr 跨平台会漂），并让"整数权重 vs 浮点参考"
这条 parity 可机械断言。

用法（采集在仓根、用仓内 venv）::

    PYTHONPATH=tools .venv/bin/python -m sgs_train.fit collect \\
        --games 4000 --players 5 --seed-start 90000 --workers 10 \\
        --out .scratch/ai-view/t08c/samples.jsonl.gz

拟合（用仓外训练 venv；numpy 只在这条命令里被 import）::

    PYTHONPATH=src:tools /home/admin2026/code/.venv-train/bin/python -m sgs_train.fit fit \\
        --samples .scratch/ai-view/t08c/samples.jsonl.gz --lambda-b 10000 \\
        --ac-report .scratch/ai-view/t08b/fit-report.json \\
        --out /tmp/weights_data.py --report .scratch/ai-view/t08c/report-1e4.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import time
from array import array
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ALTERNATIVES",
    "LAMBDA_GRID",
    "SCHEMA",
    "PairSamples",
    "collect_games",
    "fit_pairwise",
    "load_pair_samples",
    "main",
    "render_weights_data",
]

SCHEMA = "sgs.fit.samples.v2"
"""样本文件的 schema 标（v2 = 成对偏好：``c`` 选中候选 + ``o`` 其它候选）。

换字段口径必须换它：老文件不许被新拟合器静默读成新口径。
"""

ALTERNATIVES = 8
"""每条询问保留的**其它候选**上限（``K``，票面建议 6–8）。

"按值域截断"= **手写分最高**的 K 个：低分候选与选中候选的差通常很大、对成对损失的贡献
接近 0，但每条都要算一遍特征与梯度。截断只影响采集体积，不影响"选中永远在样本里"。
"""

LAMBDA_GRID: tuple[float, ...] = (1e3, 3e3, 1e4, 3e4, 1e5, 3e5)
"""B 族 λ 的候选网格（票 08c）：1e3–1e5 是预注册网格；**3e5 是 2026-11-26 用户裁定补的**。

补的理由（见票面 ``## Comments`` 与 ``## Answer``）：行为克隆口径下 1e5 仍差 D4 一条 0.1 分
margin 的用例（79/80），3e5 是第一个三条判据全过的候选。网格仍按"跑完不许改"执行一次，
不因为结果再动。"""


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


def _b_items(features: Any, query: Any, action: Any) -> list[list[Any]]:
    """一个候选的 B 族分项（同名项累加；键与值都保持既有打分的原样）。

    ``features._scored_items`` 是 :func:`sgs.agents.features.frame` 的**同一个** trace 入口
    （同一个打分器、同一条派发），``features.b_family`` 是同一个改名 / 归并函数；这里只跳过
    A / C 两族——它们在一条询问的**全部候选**上逐键相同，成对损失只约束候选差，存下来是纯浪费。
    """
    grouped = features.b_family(features._scored_items(query, action))
    return [[key, float(value)] for key, value in grouped.items()]


def _sample_row(
    seed: int, seat: int, query: Any, action: Any, y: int, alternatives: int
) -> dict[str, Any]:
    """一次询问的样本行：选中候选 + 手写分最高的 ``alternatives`` 个其它候选。"""
    from sgs.agents import features
    from sgs.agents import heuristic as heuristic_scoring

    kind = heuristic_scoring.QueryKind(query.kind)
    scorer = heuristic_scoring._SCORERS[kind]
    ranked = [
        (float(scorer(query, other)), position, other)
        for position, other in enumerate(query.legal_actions)
        if other != action
    ]
    # 平局按候选原序（确定性）：同一份样本表在任何进程里逐字节相同。
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return {
        "s": seed,
        "p": seat,
        "k": kind.name,
        "q": int(query.seq),
        "y": y,
        "v": float(scorer(query, action)),
        "c": _b_items(features, query, action),
        "o": [
            _b_items(features, query, other)
            for _score, _position, other in ranked[:alternatives]
        ],
    }


def _collect_one(payload: tuple[int, int, str, int]) -> dict[str, Any]:
    """跑**一局**并交出它的样本行（纯数据、可 pickle 回父进程）。"""
    seed, players, mode, alternatives = payload
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
            rows.append(_sample_row(seed, seat, query, action, y, alternatives))
    del _SINKS[:]
    return {"seed": seed, "error": None, "samples": rows}


def collect_games(
    seeds: Iterable[int],
    *,
    players: int = 5,
    mode: str = "identity",
    workers: int = 8,
    alternatives: int = ALTERNATIVES,
    out_path: Path,
    progress: bool = True,
) -> dict[str, Any]:
    """跑整批自弈并写 ``jsonl.gz``；返回摘要（局数 / 样本数 / 失败种子）。"""
    if alternatives < 1:
        raise SystemExit(f"--alternatives 必须 ≥ 1：{alternatives}")
    seed_list = list(seeds)
    payloads = [(seed, players, mode, alternatives) for seed in seed_list]
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
                    "alternatives": alternatives,
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
                    f"  {index}/{len(payloads)} 局 · {samples} 询问 · "
                    f"{rate:.1f} 局/s · 失败 {len(failures)}",
                    file=sys.stderr,
                )
    return {
        "schema": SCHEMA,
        "players": players,
        "mode": mode,
        "alternatives": alternatives,
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
class PairSamples:
    """一次成对拟合的样本面（紧凑平铺：候选 × 特征 + 询问 × 候选对）。

    - ``index`` / ``values`` / ``starts``：**候选**的稀疏特征（CSR 形状；候选按出现序）；
    - ``query_sign`` / ``query_value``：每个询问的结局符号（±1）与选中候选分（定 S 用）；
    - ``chosen``：每个询问的选中候选下标；
    - ``pair_query`` / ``pair_alt``：每一对 = ``(询问, 另一个候选)``（选中侧由 ``chosen`` 查）。

    用 ``array`` 而不是 Python 对象：10⁶ 量级的询问 × 9 个候选，``list[tuple[...]]`` 会
    吃掉十几 GB；平铺数组在 numpy 侧可零拷贝。
    """

    keys: dict[str, int] = field(default_factory=dict)
    index: array = field(default_factory=lambda: array("i"))
    values: array = field(default_factory=lambda: array("d"))
    starts: array = field(default_factory=lambda: array("q"))
    query_sign: array = field(default_factory=lambda: array("b"))
    query_value: array = field(default_factory=lambda: array("d"))
    chosen: array = field(default_factory=lambda: array("i"))
    pair_query: array = field(default_factory=lambda: array("i"))
    pair_alt: array = field(default_factory=lambda: array("i"))
    scale: float = 0.0
    games: int = 0

    @property
    def queries(self) -> int:
        return len(self.query_sign)

    @property
    def pairs(self) -> int:
        return len(self.pair_query)

    @property
    def candidates(self) -> int:
        return len(self.starts) - 1


def load_pair_samples(path: Path) -> PairSamples:
    """读 v2 ``jsonl.gz``；``S`` 取**选中候选分 > 0** 的中位数（口径同 08b）。"""
    store = PairSamples()
    seeds: set[int] = set()
    opener = gzip.open if path.suffix == ".gz" else open

    def add(entries: Sequence[Sequence[Any]], where: str) -> int:
        candidate = len(store.starts)
        store.starts.append(len(store.index))
        if not entries:
            raise SystemExit(f"{where} 的候选没有任何 B 分项：B 族只能从打分器 trace 派生")
        for key, value in entries:
            found = store.keys.get(str(key))
            if found is None:
                found = len(store.keys)
                store.keys[str(key)] = found
            store.index.append(found)
            store.values.append(float(value))
        return candidate

    with opener(path, "rt", encoding="utf-8") as handle:
        header = json.loads(handle.readline())
        if header.get("schema") != SCHEMA:
            raise SystemExit(f"样本 schema 不是 {SCHEMA}：{header.get('schema')!r}")
        for line_number, line in enumerate(handle, start=2):
            row = json.loads(line)
            query = len(store.query_value)
            seeds.add(int(row["s"]))
            store.query_sign.append(int(row["y"]))
            store.query_value.append(float(row["v"]))
            where = f"seed={row['s']} seat={row['p']} seq={row['q']}"
            store.chosen.append(add(row["c"], where))
            for alternative in row["o"]:
                store.pair_query.append(query)
                store.pair_alt.append(add(alternative, where))
    store.starts.append(len(store.index))
    store.games = len(seeds)
    positive = [float(value) for value in store.query_value if value > 0]
    if not positive:
        raise SystemExit("样本里没有正的选中候选分：无法定目标量纲 S")
    store.scale = statistics.median(positive)
    if store.scale <= 0:
        raise SystemExit("目标量纲 S 非正：样本表坏了")
    return store


def _matrix(np: Any, samples: PairSamples) -> Any:
    """候选特征 → 稠密 ``(候选数, 键数)`` 矩阵（B 键只有几十个，稠密比稀疏快得多）。

    每个候选至少一个分项（:func:`load_pair_samples` 已断言），故 ``starts`` 严格递增。
    """
    starts = np.frombuffer(samples.starts, dtype=np.int64)
    index = np.frombuffer(samples.index, dtype=np.int32)
    values = np.frombuffer(samples.values, dtype=np.float64)
    candidates = len(starts) - 1
    counts = np.diff(starts)
    if np.any(counts <= 0):
        raise SystemExit("样本里有空候选：starts 必须严格递增")
    rows = np.repeat(np.arange(candidates, dtype=np.int64), counts)
    matrix = np.zeros((candidates, len(samples.keys)), dtype=np.float64)
    matrix[rows, index.astype(np.int64)] = values
    return matrix


def _sigmoid(np: Any, z: Any) -> Any:
    """数值稳定的 logistic（正负两段各自 exp，避免 ``exp(大正数)`` 溢出）。"""
    out = np.empty_like(z)
    positive = z >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
    exponent = np.exp(z[~positive])
    out[~positive] = exponent / (1.0 + exponent)
    return out


def _objective(
    np: Any, matrix: Any, pair_chosen: Any, pair_alt: Any, y_pair: Any, w: Any,
    anchor: Any, lambda_b: float,
) -> float:
    """成对 logistic 损失 + 锚先验（标量；线搜索与收敛判据共用）。"""
    scores = matrix @ w
    delta = scores[pair_chosen] - scores[pair_alt]
    z = -y_pair * delta
    loss = float(np.logaddexp(0.0, z).sum())
    gap = w - anchor
    return loss + lambda_b * float(gap @ gap)


def _gradient_hessian(
    np: Any, matrix: Any, pair_chosen: Any, pair_alt: Any, y_pair: Any, w: Any,
    anchor: Any, lambda_b: float, *, chunk: int = 262_144,
) -> tuple[Any, Any, float, float]:
    """返回 ``(∇, ∇², 总目标, 成对损失)``；Hessian 按对分批累加（一次不物化全部候选差）。

    Hessian = ``Σ h_p d_p d_pᵀ + 2λI``（``d_p`` = 选中候选 − 另一候选的特征差、
    ``h_p`` = σ(1−σ)）；只是候选**差**的函数，故没有截距、也不受"多数决策同分"影响。
    """
    scores = matrix @ w
    delta = scores[pair_chosen] - scores[pair_alt]
    z = -y_pair * delta
    data_loss = float(np.logaddexp(0.0, z).sum())
    gap = w - anchor
    loss = data_loss + lambda_b * float(gap @ gap)
    probability = _sigmoid(np, z)
    coefficient = -y_pair * probability
    candidates = matrix.shape[0]
    per_candidate = np.bincount(pair_chosen, weights=coefficient, minlength=candidates)
    per_candidate -= np.bincount(pair_alt, weights=coefficient, minlength=candidates)
    gradient = matrix.T @ per_candidate + 2.0 * lambda_b * (w - anchor)

    dim = matrix.shape[1]
    hessian = np.zeros((dim, dim), dtype=np.float64)
    # 链式法则里 dz/dw = −y·d，故二阶项带 y²（y = ±S，S ≠ 1：漏掉它 Hessian 会小 S² 倍）。
    weight = probability * (1.0 - probability) * (y_pair * y_pair)
    total = len(pair_chosen)
    for start in range(0, total, chunk):
        stop = min(start + chunk, total)
        difference = matrix[pair_chosen[start:stop]] - matrix[pair_alt[start:stop]]
        hessian += difference.T @ (difference * weight[start:stop, None])
    hessian = hessian + 2.0 * lambda_b * np.eye(dim)
    return gradient, hessian, loss, data_loss


def fit_pairwise(
    samples: PairSamples,
    *,
    lambda_b: float,
    max_iterations: int = 60,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """单个 ``λ_B`` 的成对拟合（Newton + Armijo；确定性、不取随机数）；返回报告。"""
    import numpy as np

    from sgs.agents import weights as weight_module

    if lambda_b <= 0:
        raise SystemExit(f"λ_B 必须为正：{lambda_b}")
    keys = tuple(samples.keys)
    if not keys:
        raise SystemExit("样本里没有任何 B 键")
    for key in keys:
        if weight_module.family(key) != weight_module.FAMILY_B:
            raise SystemExit(f"成对拟合只收 B 族键，收到 {key!r}")
    anchor = np.asarray([weight_module.anchor(key) for key in keys], dtype=np.float64)
    matrix = _matrix(np, samples)
    pair_chosen = np.frombuffer(samples.chosen, dtype=np.int32)[
        np.frombuffer(samples.pair_query, dtype=np.int32)
    ]
    pair_alt = np.frombuffer(samples.pair_alt, dtype=np.int32)
    # 行为克隆：选中候选恒为被偏好方，结局不进方向位（用户 2026-11-26 裁定）。
    # 样本表里的 query_sign 仍保留每局胜负，供后续票做结局加权；本票等权。
    y_pair = np.full(len(samples.pair_query), samples.scale, dtype=np.float64)

    w = anchor.copy()
    history: list[dict[str, Any]] = []
    started = time.perf_counter()
    converged = False
    loss = float("nan")
    for iteration in range(max_iterations):
        gradient, hessian, loss, data_loss = _gradient_hessian(
            np, matrix, pair_chosen, pair_alt, y_pair, w, anchor, lambda_b
        )
        norm = float(np.max(np.abs(gradient)))
        history.append({"iteration": iteration, "loss": loss, "gradient_inf": norm})
        if norm <= tolerance:
            converged = True
            break
        try:
            direction = np.linalg.solve(hessian, -gradient)
        except np.linalg.LinAlgError as error:
            raise SystemExit(f"Newton 步的 Hessian 奇异（λ_B={lambda_b}）：{error}") from error
        slope = float(gradient @ direction)
        if slope >= 0:
            raise SystemExit(f"Newton 方向不是下降方向（λ_B={lambda_b}）：{slope}")
        step = 1.0
        for _ in range(30):
            candidate = w + step * direction
            value = _objective(
                np, matrix, pair_chosen, pair_alt, y_pair, candidate, anchor, lambda_b
            )
            if value <= loss + 1e-4 * step * slope:
                break
            step *= 0.5
        else:
            raise SystemExit(f"线搜索失败（λ_B={lambda_b}，第 {iteration} 步）")
        if float(np.max(np.abs(step * direction))) <= 1e-12:
            w = candidate
            converged = True
            break
        w = candidate
    else:
        gradient, hessian, loss, data_loss = _gradient_hessian(
            np, matrix, pair_chosen, pair_alt, y_pair, w, anchor, lambda_b
        )

    fitted = {
        key: float(value)
        for key, value in zip(keys, w, strict=True)
        if float(value) != float(weight_module.anchor(key))
    }
    quantized = {
        key: round(value * weight_module.WEIGHT_SCALE) for key, value in fitted.items()
    }
    return {
        "schema": "sgs.fit.pairwise.report.v1",
        "samples": samples.queries,
        "pairs": samples.pairs,
        "games": samples.games,
        "keys": len(keys),
        "lambda_b": float(lambda_b),
        "target_scale": samples.scale,
        "target": "chosen-over-alternative",
        "outcome": "not used in the pair direction (behavior cloning)",
        "loss": float(loss),
        "pair_loss": float(data_loss),
        "mean_pair_loss": (
            float(data_loss / samples.pairs) if samples.pairs else float("nan")
        ),
        "iterations": len(history),
        "converged": converged,
        "gradient_inf": history[-1]["gradient_inf"] if history else float("nan"),
        "elapsed_s": round(time.perf_counter() - started, 1),
        "history": history,
        "float_weights": fitted,
        "integer_weights": quantized,
        "anchor": {
            key: float(value) for key, value in zip(keys, anchor, strict=True)
        },
    }


def render_weights_data(
    report: dict[str, Any], ac_rows: Iterable[tuple[str, int]] = ()
) -> str:
    """把报告渲染成 ``src/sgs/agents/weights_data.py`` 的**整块**源码。

    B 族来自成对拟合（本票），A / C 族由 ``--ac-report`` 从 08b 的首轮报告原样搬入
    （票面：A/C 是票 09 的输入，与决策无关）。行长固定、键序按字典序——同一份报告渲染
    两次逐字节相同。
    """
    shared = sorted(report["integer_weights"].items())
    position = sorted(tuple(row) for row in ac_rows)
    for key, _value in shared:
        if not key.startswith("b|"):
            raise SystemExit(f"成对拟合的报告里混进了非 B 键：{key!r}")
    for key, _value in position:
        if key.startswith("b|"):
            raise SystemExit(f"A / C 块里混进了 B 键：{key!r}")
    lines = [
        '"""拟合出来的整数权重（`ai-view` 票 08c；生成文件，**不要手改**）。',
        "",
        "由 ``tools/sgs_train/fit.py`` 整块重写；每行 = ``(键, round(w × 1000))``。",
        "B 族 = 成对偏好拟合（票 08c）；A / C 族沿用 08b 第一轮的值（票 09 的输入）。",
        f"样本 {report['samples']} 条询问 / {report['pairs']} 对 · 键 {report['keys']} 个 · "
        f"λ_B = {report['lambda_b']!r} · 目标量纲 S = {report['target_scale']!r}"
        "（行为克隆：选中动作恒被偏好）。",
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
    lines.extend(f"    ({key!r}, {value})," for key, value in position)
    lines.append(")")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _ac_rows(path: Path) -> list[tuple[str, int]]:
    """从 08b 的拟合报告里取 A / C 族的整数值（B 族由本票重拟合，不收）。"""
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = report.get("integer_weights")
    if not isinstance(rows, dict):
        raise SystemExit(f"{path} 不是拟合报告（缺 integer_weights）")
    return [
        (str(key), int(value))
        for key, value in rows.items()
        if not str(key).startswith("b|")
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sgs_train.fit", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="跑自弈批并写样本文件（仓内 .venv）。")
    collect.add_argument("--games", type=int, required=True)
    collect.add_argument("--players", type=int, default=5)
    collect.add_argument("--mode", default="identity")
    collect.add_argument("--seed-start", type=int, default=90000)
    collect.add_argument("--workers", type=int, default=8)
    collect.add_argument(
        "--alternatives",
        type=int,
        default=ALTERNATIVES,
        help=f"每条询问保留的其它候选上限 K（缺省 {ALTERNATIVES}）",
    )
    collect.add_argument("--out", type=Path, required=True)
    collect.add_argument("--summary", type=Path, default=None)

    fit = sub.add_parser("fit", help="拟合并打印 weights_data.py（训练 venv）。")
    fit.add_argument("--samples", type=Path, required=True)
    fit.add_argument("--lambda-b", type=float, required=True, help="B 族的 λ（预注册网格之一）")
    fit.add_argument("--out", type=Path, default=None)
    fit.add_argument("--report", type=Path, default=None)
    fit.add_argument(
        "--ac-report",
        type=Path,
        default=None,
        help="08b 第一轮报告：把它的 A / C 族整数值原样并进 weights_data.py",
    )
    fit.add_argument("--max-iterations", type=int, default=60)
    fit.add_argument("--tolerance", type=float, default=1e-6)

    args = parser.parse_args(argv)
    if args.command == "collect":
        summary = collect_games(
            range(args.seed_start, args.seed_start + args.games),
            players=args.players,
            mode=args.mode,
            workers=args.workers,
            alternatives=args.alternatives,
            out_path=args.out,
        )
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        print(text)
        if args.summary is not None:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(text + "\n", encoding="utf-8")
        return 0

    samples = load_pair_samples(args.samples)
    report = fit_pairwise(
        samples,
        lambda_b=args.lambda_b,
        max_iterations=args.max_iterations,
        tolerance=args.tolerance,
    )
    source = render_weights_data(
        report,
        () if args.ac_report is None else _ac_rows(args.ac_report),
    )
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
