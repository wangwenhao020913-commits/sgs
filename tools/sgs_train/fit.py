"""离线拟合权重（`ai-view` 票 08c；spec §2.7 / §2.8）：自弈样本 → 整数权重。

**两段式**（采集贵、拟合便宜，spec §2.8）：

1. ``collect`` —— 跑自弈批，把**每个询问**的
   ``(选中候选的 B 分项, 至多 K 个其它候选的 B 分项, 阵营结局)`` 写成 ``jsonl.gz``。
   这一段必须用**仓内** ``.venv``（它要 ``import sgs``）；多进程采集，进程数 = ``--workers``。
2. ``fit`` —— 读样本、拟合、量化成**整数**、打印 ``weights_data.py`` 的整块源码。这一段用
   **训练 venv**（numpy；§2.8 的常设约束：训练栈不进 ``SGS/.venv``）：仓库外那个
   ``../.venv-train``，命令见票面。

**拟合目标**（票 08c：成对偏好 / 行为克隆；`ai-train` 票 01 加三个旋钮）：

    minimize  Σ_pairs w_pair·softplus(−S·(s_chosen − s_alt)) + λ_B·Σ(w − anchor)²
    s.t.      ‖w − anchor‖∞ ≤ ε

- 每一对是**同一次询问**里的"选中动作 vs 另一个候选"：**选中动作恒为被偏好的一方**
  （行为克隆），``S`` = 样本里**选中候选分的中位数**（08b 的同一口径）。``w = 1`` 必须读出
  "手写价"，故目标量纲取分数本身，而不是 ±1（理由见 08b 的模块 docstring）。
- **结局只作样本权重**（票 01）：``w_pair`` = 1.0（该座位所在方胜）/ ``lose_weight``（负）。
  ``lose_weight = 1.0``（默认）就是 08c 的等权读数；``0.0`` = 只用胜方 pair。样本表里每局的
  ``y = ±1``（采集口径不变）只在这里被读，**不进** softplus 的方向位。
- **主正则 = 信任域**（票 01）：λ 锚先验保留，但每次 Newton 步把 ``w`` 投影到
  ``[anchor − ε, anchor + ε]``（贴界键只许朝域内走，其余键在自由集上重解 Newton），
  Armijo 线搜索走投影后的可行方向；收敛判据在 ``ε > 0`` 时取投影梯度（KKT 残差）。
  ``ε = 0``（默认）= 关闭，逐字节复现 08c 的落仓权重。
- **按局切分**（票 01）：``valid = seed % valid_mod == 0``；``valid_mod = 0``（默认）不切分。
  拟合只用 train，valid 只报**未加权** pair log loss 与 accuracy，不参与拟合。
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
import hashlib
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

from . import semantic_constraints

__all__ = [
    "ALTERNATIVES",
    "DEFAULT_LOSE_WEIGHT",
    "DEFAULT_TRUST_REGION",
    "DEFAULT_VALID_MOD",
    "LAMBDA_GRID",
    "SCHEMA",
    "PairSamples",
    "collect_games",
    "fit_pairwise",
    "load_pair_samples",
    "main",
    "pair_weights_from_sign",
    "project_weights_to_trust_region",
    "render_weights_data",
    "split_train_valid_masks",
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

DEFAULT_LOSE_WEIGHT = 1.0
"""负方 pair 的默认权重：1.0 = 等权（08c 的读数；票 01 的默认路径必须逐字节复现它）。"""

DEFAULT_TRUST_REGION = 0.0
"""信任域默认 ``ε``：0 = 关闭（08c 的无投影路径；票 01 的默认路径）。"""

DEFAULT_VALID_MOD = 0
"""valid 切分默认：0 = 不切分（全量拟合；票 01 的默认路径）。"""


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


# 纯 Python 辅助（票 01）：门禁的仓内 venv **没有 numpy**，`tests/test_ai_train_fit.py`
# 只测这一段；numpy 只在 `fit_pairwise` 里被 import，且口径必须与这里逐式一致。

def pair_weights_from_sign(
    query_sign: Iterable[int], lose_weight: float
) -> list[float]:
    """结局符号 → 每对的样本权重：胜方 1.0，负方 ``lose_weight``（票 01）。

    ``query_sign`` 是样本行里的 ``y``（每局胜负，±1）；``0`` 或负数一律按负方处理
    （采集口径只写 ±1，这里只钉容错方向，不新增语义）。
    """
    winner = 1.0
    loser = float(lose_weight)
    return [winner if int(sign) > 0 else loser for sign in query_sign]


def project_weights_to_trust_region(
    fitted: Sequence[float], anchor: Sequence[float], epsilon: float
) -> list[float]:
    """逐键投影到 ``[a − ε, a + ε]``（票 01 的 ``‖w − anchor‖∞ ≤ ε``）。

    ``ε <= 0`` = **关闭**：原样返回（不是投影到锚——08c 的默认路径必须无投影）。
    端点用 ``min/max`` 夹取，确定性、无浮点随机源。
    """
    values = [float(value) for value in fitted]
    if epsilon <= 0.0:
        return values
    limits = [float(value) for value in anchor]
    if len(values) != len(limits):
        raise ValueError(f"权重与锚的键数不同：{len(values)} != {len(limits)}")
    return [
        min(max(value, base - epsilon), base + epsilon)
        for value, base in zip(values, limits, strict=True)
    ]


def split_train_valid_masks(
    query_seed: Iterable[int], valid_mod: int
) -> tuple[list[bool], list[bool]]:
    """按**局种子**切 train / valid：``seed % valid_mod == 0`` 进 valid（票 01）。

    返回 ``(train_mask, valid_mask)``，每个询问一个布尔，二者互补；``valid_mod <= 0`` =
    不切分（全部进 train、valid 为空）。切分单位是**局**而不是样本：同一局的所有询问
    必须落在同一侧，否则同一局内的相关样本会同时进训练和验证。
    """
    seeds = [int(seed) for seed in query_seed]
    if valid_mod <= 0:
        return [True] * len(seeds), [False] * len(seeds)
    valid = [seed % valid_mod == 0 for seed in seeds]
    return [not flag for flag in valid], valid


@dataclass
class PairSamples:
    """一次成对拟合的样本面（紧凑平铺：候选 × 特征 + 询问 × 候选对）。

    - ``index`` / ``values`` / ``starts``：**候选**的稀疏特征（CSR 形状；候选按出现序）；
    - ``query_sign`` / ``query_seed`` / ``query_value``：每个询问的结局符号（±1）、所在
      **局种子**（train / valid 按它切）与选中候选分（定 S 用）；
    - ``chosen``：每个询问的选中候选下标；
    - ``pair_query`` / ``pair_alt``：每一对 = ``(询问, 另一个候选)``（选中侧由 ``chosen`` 查）；
    - ``data_sha256``：样本文件的字节摘要（报表留痕；``PairSamples()`` 手建时为空串）。

    用 ``array`` 而不是 Python 对象：10⁶ 量级的询问 × 9 个候选，``list[tuple[...]]`` 会
    吃掉十几 GB；平铺数组在 numpy 侧可零拷贝。
    """

    keys: dict[str, int] = field(default_factory=dict)
    index: array = field(default_factory=lambda: array("i"))
    values: array = field(default_factory=lambda: array("d"))
    starts: array = field(default_factory=lambda: array("q"))
    query_sign: array = field(default_factory=lambda: array("b"))
    query_seed: array = field(default_factory=lambda: array("q"))
    query_value: array = field(default_factory=lambda: array("d"))
    chosen: array = field(default_factory=lambda: array("i"))
    pair_query: array = field(default_factory=lambda: array("i"))
    pair_alt: array = field(default_factory=lambda: array("i"))
    scale: float = 0.0
    games: int = 0
    data_sha256: str = ""

    @property
    def queries(self) -> int:
        return len(self.query_sign)

    @property
    def pairs(self) -> int:
        return len(self.pair_query)

    @property
    def candidates(self) -> int:
        return len(self.starts) - 1


def _file_sha256(path: Path) -> str:
    """样本文件的字节摘要（报表 ``data_sha256``；只读原始字节，不改读法）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_pair_samples(path: Path) -> PairSamples:
    """读 v2 ``jsonl.gz``；``S`` 取**选中候选分 > 0** 的中位数（口径同 08b）。

    同时把每行的局种子（``s``）收进 ``query_seed``（票 01 的按局切分用），并对**文件字节**
    取 sha256 存进 ``data_sha256``——报表留痕到具体一份样本表，不换 schema、不新增字段。
    """
    store = PairSamples()
    store.data_sha256 = _file_sha256(path)
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
            store.query_seed.append(int(row["s"]))
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
    np: Any, matrix: Any, pair_chosen: Any, pair_alt: Any, y_pair: Any, pair_weight: Any,
    w: Any, anchor: Any, lambda_b: float,
) -> float:
    """加权成对 logistic 损失 + 锚先验（标量；线搜索与收敛判据共用）。"""
    scores = matrix @ w
    delta = scores[pair_chosen] - scores[pair_alt]
    z = -y_pair * delta
    loss = float((pair_weight * np.logaddexp(0.0, z)).sum())
    gap = w - anchor
    return loss + lambda_b * float(gap @ gap)


def _gradient_hessian(
    np: Any, matrix: Any, pair_chosen: Any, pair_alt: Any, y_pair: Any, pair_weight: Any,
    w: Any, anchor: Any, lambda_b: float, *, chunk: int = 262_144,
) -> tuple[Any, Any, float, float]:
    """返回 ``(∇, ∇², 总目标, 加权成对损失)``；Hessian 按对分批累加（不物化全部候选差）。

    Hessian = ``Σ w_pair·h_p d_p d_pᵀ + 2λI``（``d_p`` = 选中候选 − 另一候选的特征差、
    ``h_p`` = σ(1−σ)）；只是候选**差**的函数，故没有截距、也不受"多数决策同分"影响。
    ``w_pair`` 是每对的结局权重（票 01）：梯度与 Hessian 都乘它，``1.0`` 时与 08c 逐位相同。
    """
    scores = matrix @ w
    delta = scores[pair_chosen] - scores[pair_alt]
    z = -y_pair * delta
    data_loss = float((pair_weight * np.logaddexp(0.0, z)).sum())
    gap = w - anchor
    loss = data_loss + lambda_b * float(gap @ gap)
    probability = _sigmoid(np, z)
    coefficient = -y_pair * probability * pair_weight
    candidates = matrix.shape[0]
    per_candidate = np.bincount(pair_chosen, weights=coefficient, minlength=candidates)
    per_candidate -= np.bincount(pair_alt, weights=coefficient, minlength=candidates)
    gradient = matrix.T @ per_candidate + 2.0 * lambda_b * (w - anchor)

    dim = matrix.shape[1]
    hessian = np.zeros((dim, dim), dtype=np.float64)
    # 链式法则里 dz/dw = −y·d，故二阶项带 y²（y = ±S，S ≠ 1：漏掉它 Hessian 会小 S² 倍）。
    weight = probability * (1.0 - probability) * (y_pair * y_pair) * pair_weight
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
    lose_weight: float = DEFAULT_LOSE_WEIGHT,
    trust_region: float = DEFAULT_TRUST_REGION,
    valid_mod: int = DEFAULT_VALID_MOD,
    max_iterations: int = 60,
    tolerance: float = 1e-6,
    constraints: Sequence[Any] = (),
) -> dict[str, Any]:
    """单个 ``λ_B`` 的成对拟合（Newton + 投影 + Armijo；确定性、不取随机数）。

    三个旋钮（票 01）都有"复现 08c"的默认值：``lose_weight=1.0`` / ``trust_region=0`` /
    ``valid_mod=0``。默认路径的数值（含迭代轨迹）与 08c **逐位相同**——这是本票的硬验收。

    - 加权：每对乘 ``w_pair``（胜 1.0 / 负 ``lose_weight``），梯度与 Hessian 同步乘；
    - 信任域：``trust_region > 0`` 时每次 Newton 步投影到 ``[anchor−ε, anchor+ε]``；贴界键
      按方向进活跃集（只许朝域内走），其余键在自由集上重解 Newton，线搜索走投影后的可行
      方向（必要时退到投影梯度）；收敛判据换成投影梯度（KKT 残差，``history[*].kkt_inf``）；
    - 切分：``valid_mod > 0`` 时只用 train 拟合；``valid`` 只算未加权 pair log loss 与
      accuracy（严格 ``选中分 > 其它分``），不参与拟合、不当判据。
    """
    import numpy as np

    from sgs.agents import weights as weight_module

    if lambda_b <= 0:
        raise SystemExit(f"λ_B 必须为正：{lambda_b}")
    if not 0.0 <= lose_weight <= 1.0:
        raise SystemExit(f"--lose-weight 必须在 [0, 1]（1.0 = 08c 等权）：{lose_weight}")
    if trust_region < 0.0:
        raise SystemExit(f"--trust-region 必须 ≥ 0（0 = 关闭）：{trust_region}")
    if valid_mod < 0:
        raise SystemExit(f"--valid-mod 必须 ≥ 0（0 = 不切分）：{valid_mod}")
    keys = tuple(samples.keys)
    if not keys:
        raise SystemExit("样本里没有任何 B 键")
    for key in keys:
        if weight_module.family(key) != weight_module.FAMILY_B:
            raise SystemExit(f"成对拟合只收 B 族键，收到 {key!r}")
    constraints = tuple(constraints)
    for constraint in constraints:
        if tuple(constraint.keys) != keys:
            raise SystemExit(
                f"语义约束 {constraint.name!r} 的键序与样本键序不一致："
                "约束必须由同一份 samples.keys 展开"
            )
    anchor = np.asarray([weight_module.anchor(key) for key in keys], dtype=np.float64)
    anchor_values = anchor.tolist()
    matrix = _matrix(np, samples)

    pair_query = np.frombuffer(samples.pair_query, dtype=np.int32)
    pair_chosen = np.frombuffer(samples.chosen, dtype=np.int32)[pair_query]
    pair_alt = np.frombuffer(samples.pair_alt, dtype=np.int32)
    query_sign = np.frombuffer(samples.query_sign, dtype=np.int8)
    query_seed = np.frombuffer(samples.query_seed, dtype=np.int64)
    if len(query_sign) != samples.queries or len(query_seed) != samples.queries:
        raise SystemExit("样本平铺数组长度不一致：query_sign / query_seed / queries")
    # 行为克隆方向：选中候选恒为被偏好方（用户 2026-11-26 裁定）；每局的胜负
    # （query_sign）只作 pair 的样本权重（票 01），不进 softplus 的方向位。
    y_pair = np.full(len(pair_query), samples.scale, dtype=np.float64)
    pair_weight = np.asarray(
        pair_weights_from_sign(query_sign[pair_query], lose_weight), dtype=np.float64
    )

    train_query_mask, valid_query_mask = split_train_valid_masks(query_seed, valid_mod)
    train_query = np.asarray(train_query_mask, dtype=bool)
    valid_query = np.asarray(valid_query_mask, dtype=bool)
    train_pairs = train_query[pair_query]
    valid_pairs = valid_query[pair_query]
    if not bool(train_pairs.any()):
        raise SystemExit("train 集没有成对样本：检查 --valid-mod 与样本种子")
    if valid_mod > 0:
        if not bool(valid_query.any()):
            raise SystemExit(f"valid 集为空：seed % {valid_mod} == 0 的局一条都没有")
        if not bool(valid_pairs.any()):
            raise SystemExit("valid 集没有成对样本（全是被截断的单候选询问）")
    train_chosen = pair_chosen[train_pairs]
    train_alt = pair_alt[train_pairs]
    train_y = y_pair[train_pairs]
    train_weight = pair_weight[train_pairs]

    lower_values = (
        [value - trust_region for value in anchor_values] if trust_region > 0.0 else None
    )
    upper_values = (
        [value + trust_region for value in anchor_values] if trust_region > 0.0 else None
    )

    def project(point: Any) -> Any:
        """投影到信任域（有约束时再投影到语义约束）；默认路径逐位保持 08c。"""
        if trust_region <= 0.0 and not constraints:
            return point
        if trust_region > 0.0:
            point = np.asarray(
                project_weights_to_trust_region(point.tolist(), anchor_values, trust_region),
                dtype=np.float64,
            )
        if constraints:
            point = np.asarray(
                semantic_constraints.project_to_feasible(
                    point.tolist(), constraints, lower_values, upper_values
                ),
                dtype=np.float64,
            )
        return point

    w = anchor.copy()
    if constraints:
        # 锚点是约束的可行点（D4 的 +0.1 手写 margin 在锚点 gap=0.1）；通用约束也先投影一次。
        w = project(w)
    history: list[dict[str, Any]] = []
    started = time.perf_counter()
    converged = False
    loss = float("nan")
    for iteration in range(max_iterations):
        gradient, hessian, loss, data_loss = _gradient_hessian(
            np, matrix, train_chosen, train_alt, train_y, train_weight,
            w, anchor, lambda_b,
        )
        raw_inf = float(np.max(np.abs(gradient)))
        entry: dict[str, Any] = {
            "iteration": iteration,
            "loss": loss,
            "gradient_inf": raw_inf,
        }
        if trust_region > 0.0:
            # 约束面上的收敛判据 = 投影梯度（KKT 残差）：贴界键的原始梯度带着乘子，
            # 不会归零（λ 锚项在边界上仍有 ~2λε），拿它当收敛判据会空转到 max_iterations。
            kkt_inf = float(np.max(np.abs(w - project(w - gradient))))
            entry["kkt_inf"] = kkt_inf
            norm = kkt_inf
        else:
            norm = raw_inf
        history.append(entry)
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
        if constraints and trust_region > 0.0:
            # 语义约束活跃时，在"约束面 + 盒子活跃集"上解**等式约束 Newton**：
            #   [H_ff  a_f] [d_f]   [-g_f]
            #   [a_fᵀ   0 ] [λ ] = [ 0  ]
            # 方向切于约束面；投影线搜索负责盒子与数值重整。只走无约束 Newton + 投影
            # 时实测 60 步 KKT 停在 0.1（约束面与盒子面把方向削得只剩极小步），
            # 故活跃面上必须解等式系统。
            upper = anchor + trust_region
            lower = anchor - trust_region
            blocked = ((w >= upper - 1e-12) & (gradient < 0.0)) | (
                (w <= lower + 1e-12) & (gradient > 0.0)
            )
            free = ~blocked
            constraint = constraints[0]
            gap = constraint.gap(w.tolist())
            if gap <= constraint.margin + 1e-7 and bool(free.any()):
                coefficients = np.asarray(constraint.coefficients, dtype=np.float64)
                a_free = coefficients[free]
                if float(np.max(np.abs(a_free))) > 0.0:
                    count = int(free.sum())
                    kkt = np.zeros((count + 1, count + 1), dtype=np.float64)
                    kkt[:count, :count] = hessian[np.ix_(free, free)]
                    kkt[:count, count] = a_free
                    kkt[count, :count] = a_free
                    rhs = np.zeros(count + 1, dtype=np.float64)
                    rhs[:count] = -gradient[free]
                    try:
                        solution = np.linalg.solve(kkt, rhs)
                    except np.linalg.LinAlgError:
                        solution = None
                    if solution is not None:
                        reduced = np.zeros_like(w)
                        reduced[free] = solution[:count]
                        direction = reduced
                    else:
                        direction = project(w - gradient) - w
                else:
                    direction = project(w - gradient) - w
            # 约束不在边界：保留无约束 Newton；投影线搜索会把越界的第一步拉回边界。
            slope = float(gradient @ direction)
            if slope >= 0.0:
                direction = project(w - gradient) - w
                slope = float(gradient @ direction)
            if slope >= 0.0:
                converged = True
                break
            if np.array_equal(project(w + direction), w):
                direction = project(w - gradient) - w
                slope = float(gradient @ direction)
                if slope >= 0.0:
                    converged = True
                    break
            step_direction = direction
        elif trust_region > 0.0:
            # 活跃集：贴上界且梯度想再往上（g < 0）、贴下界且想再往下（g > 0）的键**钉住**；
            # 其余键在自由集上重解 Newton——整步被约束面挡住时若直接退到投影梯度，
            # 收敛会慢几十倍。判据用**梯度**而不是未约束的 Newton 方向：自由集解出来的
            # 方向可能把某个贴界键推出去，投影会把它整段削掉（Armijo 随即失败）。
            upper = anchor + trust_region
            lower = anchor - trust_region
            blocked = ((w >= upper - 1e-12) & (gradient < 0.0)) | (
                (w <= lower + 1e-12) & (gradient > 0.0)
            )
            free = ~blocked
            if not bool(free.all()):
                reduced: Any = None
                if bool(free.any()):
                    try:
                        reduced = np.zeros_like(direction)
                        reduced[free] = np.linalg.solve(
                            hessian[np.ix_(free, free)], -gradient[free]
                        )
                    except np.linalg.LinAlgError:
                        reduced = None
                if reduced is None or float(gradient @ reduced) >= 0.0:
                    # 兜底：投影梯度（凸集投影保证下降）；再不行 = 约束面上的驻点。
                    reduced = project(w - gradient) - w
                direction = reduced
            slope = float(gradient @ direction)
            if slope >= 0.0:
                converged = True
                break
            if np.array_equal(project(w + direction), w):
                # 整步被约束面削平（自由集耦合把某个贴界键推出去）：改走投影梯度。
                direction = project(w - gradient) - w
                slope = float(gradient @ direction)
                if slope >= 0.0:
                    converged = True
                    break
            step_direction = direction
        else:
            step_direction = direction
        step = 1.0
        for _ in range(30):
            candidate = project(w + step * step_direction)
            if trust_region > 0.0 and np.array_equal(candidate, w):
                converged = True
                break
            value = _objective(
                np, matrix, train_chosen, train_alt, train_y, train_weight,
                candidate, anchor, lambda_b,
            )
            if value <= loss + 1e-4 * step * slope:
                break
            step *= 0.5
        else:
            raise SystemExit(f"线搜索失败（λ_B={lambda_b}，第 {iteration} 步）")
        if converged:
            break
        # 无投影时保持 08c 的原式（`step * direction`），避免多余的减法舍入。
        moved = (
            float(np.max(np.abs(step * step_direction)))
            if trust_region <= 0.0
            else float(np.max(np.abs(candidate - w)))
        )
        if moved <= 1e-12:
            w = candidate
            converged = True
            break
        w = candidate
    else:
        gradient, hessian, loss, data_loss = _gradient_hessian(
            np, matrix, train_chosen, train_alt, train_y, train_weight,
            w, anchor, lambda_b,
        )

    final_gradient_inf = history[-1]["gradient_inf"] if history else float("nan")
    final_kkt = history[-1].get("kkt_inf") if history else None
    if constraints and trust_region > 0.0:
        # 约束步的终点在**最后一步之后**：最后一步可能只动了一点点但跨过了活跃集，
        # 用最后一步**开始前**的 KKT 会把已收敛的解误报成残差 0.025。终点重算一次。
        final_gradient, _hessian_final, _loss_final, _data_final = _gradient_hessian(
            np, matrix, train_chosen, train_alt, train_y, train_weight,
            w, anchor, lambda_b,
        )
        final_gradient_inf = float(np.max(np.abs(final_gradient)))
        final_kkt = float(np.max(np.abs(w - project(w - final_gradient))))
        converged = converged and final_kkt <= tolerance
    elif trust_region > 0.0 and history:
        # 约束面 / 盒子活跃集上，"投影后无位移"只是驻点近似；只有 KKT 残差到阈值才算收敛。
        converged = converged and float(history[-1].get("kkt_inf", float("inf"))) <= tolerance

    fitted = {
        key: float(value)
        for key, value in zip(keys, w, strict=True)
        if float(value) != float(weight_module.anchor(key))
    }
    quantized = {
        key: round(value * weight_module.WEIGHT_SCALE) for key, value in fitted.items()
    }

    train_pairs_count = int(train_pairs.sum())
    train_scores = matrix @ w
    train_delta = train_scores[train_chosen] - train_scores[train_alt]
    train_games = int(np.unique(query_seed[train_query]).size)
    train_block = {
        "games": train_games,
        "samples": int(train_query.sum()),
        "pairs": train_pairs_count,
        "pair_loss": float(data_loss),
        "mean_pair_loss": float(data_loss / train_pairs_count),
        "pair_weight_sum": float(train_weight.sum()),
        "accuracy": float(np.count_nonzero(train_delta > 0)) / train_pairs_count,
    }

    valid_block: dict[str, Any] | None = None
    if valid_mod > 0:
        valid_chosen = pair_chosen[valid_pairs]
        valid_alt = pair_alt[valid_pairs]
        valid_y = y_pair[valid_pairs]
        valid_scores = matrix @ w
        valid_delta = valid_scores[valid_chosen] - valid_scores[valid_alt]
        valid_pointwise = np.logaddexp(0.0, -valid_y * valid_delta)
        valid_loss = float(valid_pointwise.sum())
        valid_pairs_count = len(valid_chosen)
        valid_block = {
            "games": int(np.unique(query_seed[valid_query]).size),
            "samples": int(valid_query.sum()),
            "pairs": valid_pairs_count,
            "pair_loss": valid_loss,
            # spec §2.3：valid 只报**未加权** pair log loss（= 每对 softplus 的均值）。
            "pair_log_loss": float(valid_loss / valid_pairs_count),
            "accuracy": float(np.count_nonzero(valid_delta > 0)) / valid_pairs_count,
        }

    trust_region_bound_keys = 0
    if trust_region > 0.0:
        trust_region_bound_keys = int(
            np.count_nonzero(np.abs(np.abs(w - anchor) - trust_region) <= 1e-12)
        )

    constraint_blocks: list[dict[str, Any]] = []
    if constraints:
        integer_values = [
            float(
                quantized.get(
                    key, round(weight_module.anchor(key) * weight_module.WEIGHT_SCALE)
                )
            )
            / weight_module.WEIGHT_SCALE
            for key in keys
        ]
        constraint_blocks = [
            semantic_constraints.report_block(constraint, w.tolist(), integer_values)
            for constraint in constraints
        ]

    return {
        "schema": "sgs.fit.pairwise.report.v2",
        "samples": samples.queries,
        "pairs": samples.pairs,
        "games": samples.games,
        "keys": len(keys),
        "lambda_b": float(lambda_b),
        "lose_weight": float(lose_weight),
        "trust_region": float(trust_region),
        "valid_mod": int(valid_mod),
        "constraints": constraint_blocks,
        "trust_region_bound_keys": trust_region_bound_keys,
        "target_scale": samples.scale,
        "target": "chosen-over-alternative",
        "outcome": (
            "pair weight only (behavior cloning): winner 1.0 / loser lose_weight"
        ),
        "data_sha256": samples.data_sha256 or None,
        "loss": float(loss),
        "pair_loss": float(data_loss),
        "mean_pair_loss": float(data_loss / train_pairs_count),
        "iterations": len(history),
        "converged": converged,
        "gradient_inf": final_gradient_inf,
        "kkt_inf": final_kkt,
        "elapsed_s": round(time.perf_counter() - started, 1),
        "history": history,
        "train": train_block,
        "valid": valid_block,
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

    **配置行**（票 01）：报告里的 ``lose_weight`` / ``trust_region`` / ``valid_mod`` 非默认时，
    生成文件的模块 docstring 多一行配置读数（含 ``data_sha256``，票 02 要求落仓权重自带
    "用哪份样本拟合的"），**默认配置不写这一行**——默认路径必须与 08c 的落仓文件逐字节
    相同（票 01 验收 1）。语义约束（票 03）另占一行，记名字 / margin / 拟合 gap / 量化 gap。
    """
    shared = sorted(report["integer_weights"].items())
    position = sorted(tuple(row) for row in ac_rows)
    for key, _value in shared:
        if not key.startswith("b|"):
            raise SystemExit(f"成对拟合的报告里混进了非 B 键：{key!r}")
    for key, _value in position:
        if key.startswith("b|"):
            raise SystemExit(f"A / C 块里混进了 B 键：{key!r}")
    lose_weight = float(report.get("lose_weight", DEFAULT_LOSE_WEIGHT))
    trust_region = float(report.get("trust_region", DEFAULT_TRUST_REGION))
    valid_mod = int(report.get("valid_mod", DEFAULT_VALID_MOD))
    configuration = ""
    if (lose_weight, trust_region, valid_mod) != (
        DEFAULT_LOSE_WEIGHT,
        DEFAULT_TRUST_REGION,
        DEFAULT_VALID_MOD,
    ):
        data_sha = report.get("data_sha256")
        digest = f" · 数据 sha256 = {data_sha}" if data_sha else ""
        configuration = (
            f"配置：lose_weight = {lose_weight!r} · trust_region = {trust_region!r} · "
            f"valid_mod = {valid_mod}（默认 1.0 / 0 / 0 = 08c 等权无投影全量）{digest}。"
        )
    lines = [
        '"""拟合出来的整数权重（`ai-view` 票 08c；生成文件，**不要手改**）。',
        "",
        "由 ``tools/sgs_train/fit.py`` 整块重写；每行 = ``(键, round(w × 1000))``。",
        "B 族 = 成对偏好拟合（票 08c）；A / C 族沿用 08b 第一轮的值（票 09 的输入）。",
        f"样本 {report['samples']} 条询问 / {report['pairs']} 对 · 键 {report['keys']} 个 · "
        f"λ_B = {report['lambda_b']!r} · 目标量纲 S = {report['target_scale']!r}"
        "（行为克隆：选中动作恒被偏好）。",
    ]
    if configuration:
        lines.append(configuration)
    for block in report.get("constraints", []):
        lines.append(
            f"语义约束：{block['name']} · margin = {block['margin']!r} · "
            f"拟合 gap = {block['gap']!r} · 整数量化 gap = {block.get('integer_gap')!r}"
            f"（整数量化满足 = {block.get('integer_satisfied')}）。"
        )
    lines.extend(
        [
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
    )
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


def _build_parser() -> argparse.ArgumentParser:
    """CLI 定义（单独拆出来，测试不用跑拟合就能钉默认旋钮）。"""
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
    fit.add_argument(
        "--lose-weight",
        type=float,
        default=DEFAULT_LOSE_WEIGHT,
        help="负方 pair 的样本权重（1.0 = 08c 等权；0.0 = 只用胜方 pair）",
    )
    fit.add_argument(
        "--trust-region",
        type=float,
        default=DEFAULT_TRUST_REGION,
        help="信任域 ε：‖w − 1‖∞ ≤ ε（0 = 关闭，08c 路径）",
    )
    fit.add_argument(
        "--valid-mod",
        type=int,
        default=DEFAULT_VALID_MOD,
        help="valid = seed %% valid_mod == 0（0 = 不切分，08c 路径）",
    )
    fit.add_argument(
        "--constraint",
        action="append",
        default=[],
        choices=("d4",),
        help="语义约束（票 03；可重复，当前只登记 d4 = 古锭刀先手）",
    )
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
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
    constraints = tuple(
        semantic_constraints.build(name, samples.keys) for name in args.constraint
    )
    report = fit_pairwise(
        samples,
        lambda_b=args.lambda_b,
        lose_weight=args.lose_weight,
        trust_region=args.trust_region,
        valid_mod=args.valid_mod,
        max_iterations=args.max_iterations,
        tolerance=args.tolerance,
        constraints=constraints,
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
