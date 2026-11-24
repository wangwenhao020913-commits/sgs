"""似然表估计（`ai-view` 票 07；spec §2.6）：多版本混合 AI 对局 →
``P(行为类 | 发起身份, 观察者身份, 先手方)``。

表本身住在生产代码里（:data:`sgs.agents.identity.BEHAVIOR_LIKELIHOODS`），本工具只负责
**估计**它并打印可直接粘回去的源码片段。为什么必须用**多版本混合**：用单一策略估行为模型，
模型越像自己，越会被别的策略系统性惩罚（DeepRole 的坑，spec §2.6）。故每局按座位轮换
旧版本（``ai-vN``，经 ``ai_eval.versions`` 从 git tag 加载），一个批次里每个版本都坐过每个座位。

**采样本的口径**（与推理侧同一份代码）：

- 行为观测由 :func:`sgs.agents.identity.behavior_observations` 从**每个座位自己的视图**摊出来
  ——推理时也是它，不存在第二份分类器（train/serve skew 是这一路线的头号风险）；
- 同一条（事件, 目标, 观察者）只计一次：同一事件会在后续每个询问的 32 条窗口里重复出现，
  按窗口计数会把长局灌水；
- 每组 = ``(发起身份, 观察者身份, 行为类, 先手方)``；分母 = 同一 ``(发起身份, 观察者身份,
  先手方)`` 在**九个行为类**上的观测数（于是每行四个概率：各身份被读成这一行的条件概率，
  不是九条独立读数，见 :func:`estimate`）。**观察者身份是公开事实**（本人恒知道自己是谁），
  所以它在推理 / 训练两侧都读得到，不构成 train/serve skew。

**表之外还记什么**：样本量（每行 / 每身份）、版本 ref 与解析到的提交、牌堆档位、种子区间。
这些写进 :class:`Estimate` 的 JSON；票面的 `## Answer` 只抄读数与指针。

用法（``PYTHONPATH=tools`` 不可省，与 ``ai_eval`` 同一条）::

    PYTHONPATH=tools .venv/bin/python -m sgs_train.likelihood \
        --versions ai-v0 ai-v5 ai-v10 --games 320 --out .scratch/ai-view/likelihood.json

stdout = 可直接替换的源码片段（``BEHAVIOR_LIKELIHOODS`` 整块）；进度 / 摘要走 stderr。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sgs.agents import identity
from sgs.data import Identity, Mode
from sgs.engine import ai_stream
from sgs_batch import GameSpec, run_one

from ai_eval.versions import LoadedVersion, load_version

__all__ = [
    "DEFAULT_ALPHA",
    "Counts",
    "Estimate",
    "collect_counts",
    "estimate",
    "main",
    "render_source",
]

DEFAULT_ALPHA = 0.5
"""拉普拉斯平滑（Jeffreys 先验）：每格 +0.5 次。罕见类（濒死窗口）不会得到 0 概率。"""

PLAYERS = 5
"""默认座位数：身份局 5 人（比 4 人局多一个反贼、比 6 人局少一档，与实机验收同档）。"""

DECK_PROFILE = "standard_108"
"""默认牌堆档位：身份行为与牌堆无关，取最小档省时；实机判决批不受影响。"""


class _Capture:
    """透明包一层参与者：应答原样返回，旁路记下这一问的**视图**。"""

    def __init__(
        self,
        inner: Any,
        seat: int,
        sink: list[tuple[int, Any]],
    ) -> None:
        self._inner = inner
        self._seat = seat
        self._sink = sink

    def respond(self, query: Any) -> Any:
        self._sink.append((self._seat, query.view))
        return self._inner.respond(query)


@dataclass
class Counts:
    """一个批次的原始计数：``(发起身份, 观察者身份, 行为类, 先手方) -> 次数`` + 每行分母。

    ``counts`` 用普通 dict（插入序 = 首次出现序，确定性；本项目禁无序容器参与顺序）。
    """

    counts: dict[tuple[str, str, str, str], int] = field(default_factory=dict)
    totals: dict[tuple[str, str, str], int] = field(default_factory=dict)
    pressures: dict[tuple[str, str, str], int] = field(default_factory=dict)
    pressure_totals: dict[tuple[str, str], int] = field(default_factory=dict)
    games: int = 0
    observations: int = 0
    skipped: list[str] = field(default_factory=list)
    versions: tuple[tuple[str, str, str], ...] = ()
    """``(ref, commit, heuristic_sha256)`` 逐版本（按传入顺序）。"""

    def add(self, actor: str, viewer: str, behavior: str, context: str) -> None:
        key = (actor, viewer, behavior, context)
        self.counts[key] = self.counts.get(key, 0) + 1
        self.totals[(actor, viewer, context)] = (
            self.totals.get((actor, viewer, context), 0) + 1
        )
        self.observations += 1

    def row_total(self, actor: str, viewer: str, context: str) -> int:
        return self.totals.get((actor, viewer, context), 0)

    def add_pressure(self, actor: str, viewer: str, behavior: str, count: int) -> None:
        key = (actor, viewer, behavior)
        self.pressures[key] = self.pressures.get(key, 0) + count
        self.pressure_totals[(actor, viewer)] = (
            self.pressure_totals.get((actor, viewer), 0) + count
        )

    def pressure_row_total(self, actor: str, viewer: str) -> int:
        return self.pressure_totals.get((actor, viewer), 0)


@dataclass(frozen=True)
class Estimate:
    """一次估计的完整读数（表 + 样本量 + 版本自证）。"""

    table: dict[tuple[str, str, str], tuple[float, float, float, float]]
    pressure_table: dict[tuple[str, str], tuple[float, float, float, float]]
    counts: Counts

    def to_dict(self) -> dict[str, Any]:
        rows = []
        for context in identity.CONTEXTS:
            for behavior in identity.BEHAVIOR_CLASSES:
                for viewer in identity.IDENTITY_ORDER:
                    key = (behavior, context, viewer.value)
                    rows.append(
                        {
                            "behavior": behavior,
                            "context": context,
                            "viewer": viewer.value,
                            "probabilities": {
                                actor.value: value
                                for actor, value in zip(
                                    identity.IDENTITY_ORDER,
                                    self.table[key],
                                    strict=True,
                                )
                            },
                            "counts": {
                                actor.value: self.counts.counts.get(
                                    (actor.value, viewer.value, behavior, context), 0
                                )
                                for actor in identity.IDENTITY_ORDER
                            },
                            "total": sum(
                                self.counts.row_total(
                                    actor.value, viewer.value, context
                                )
                                for actor in identity.IDENTITY_ORDER
                            ),
                        }
                    )
        return {
            "schema": "sgs.identity_likelihood.v1",
            "alpha": DEFAULT_ALPHA,
            "players": PLAYERS,
            "deck_profile": DECK_PROFILE,
            "games": self.counts.games,
            "observations": self.counts.observations,
            "skipped": list(self.counts.skipped),
            "versions": [
                {"ref": ref, "commit": commit, "sha256": digest}
                for ref, commit, digest in self.counts.versions
            ],
            "totals": {
                f"{actor.value}|{viewer.value}|{context}": self.counts.row_total(
                    actor.value, viewer.value, context
                )
                for actor in identity.IDENTITY_ORDER
                for viewer in identity.IDENTITY_ORDER
                for context in identity.CONTEXTS
            },
            "rows": rows,
            "pressure_rows": [
                {
                    "behavior": behavior,
                    "viewer": viewer.value,
                    "probabilities": {
                        actor.value: value
                        for actor, value in zip(
                            identity.IDENTITY_ORDER,
                            self.pressure_table[(behavior, viewer.value)],
                            strict=True,
                        )
                    },
                    "counts": {
                        actor.value: self.counts.pressures.get(
                            (actor.value, viewer.value, behavior), 0
                        )
                        for actor in identity.IDENTITY_ORDER
                    },
                }
                for behavior in identity.PRESSURE_CLASSES
                for viewer in identity.IDENTITY_ORDER
            ],
        }


def _load_versions(refs: Sequence[str]) -> tuple[LoadedVersion, ...]:
    """加载全部版本；少于两个直接拒绝（"多版本混合"是本票的硬口径）。"""
    if len(refs) < 2:
        raise SystemExit("--versions 至少要两个 ref：单策略估行为表是 spec §2.6 明令禁止的")
    return tuple(load_version(ref) for ref in refs)


def _factory(
    loaded: Sequence[LoadedVersion],
    sink: list[tuple[int, Any]],
) -> Any:
    """每个座位一个轮换版本的参与者；版本按 ``(局号 + 座位)`` 轮换，批次内均衡。"""

    def build(spec: GameSpec) -> list[_Capture]:
        return [
            _Capture(
                loaded[(spec.seed + seat) % len(loaded)].player_class(
                    ai_stream(spec.seed, seat)
                ),
                seat,
                sink,
            )
            for seat in range(spec.players)
        ]

    return build


def collect_counts(
    *,
    versions: Sequence[str],
    games: int,
    seed_base: int = 910_000,
    players: int = PLAYERS,
    deck_profile: str = DECK_PROFILE,
    progress: Any = None,
) -> Counts:
    """跑 ``games`` 局轮换版本的对局并累计行为观测。

    ``progress(done, total)`` 可选（出进度；CLI 用 stderr）。返回的 :class:`Counts` 里
    ``skipped`` 记没打完 / 报错的种子——**不静默缩批**，摘要里照打。
    """
    if games <= 0:
        raise SystemExit(f"--games 必须是正整数，得到 {games}")
    loaded = _load_versions(versions)
    counts = Counts(
        versions=tuple(
            (item.ref, item.commit, item.heuristic_sha256) for item in loaded
        )
    )
    for offset in range(games):
        seed = seed_base + offset
        sink: list[tuple[int, Any]] = []
        result = run_one(
            GameSpec(
                seed=seed,
                mode=Mode.IDENTITY,
                players=players,
                deck_profile=deck_profile,
            ),
            participants_factory=_factory(loaded, sink),
        )
        if result.error or not result.finished:
            counts.skipped.append(f"seed={seed}: {result.error or '未到终局'}")
            if progress is not None:
                progress(offset + 1, games)
            continue
        truth = {
            seat.seat: Identity(seat.identity)
            for seat in result.seats
            if seat.identity is not None
        }
        # 同一（事件, 目标, 观察者）只计一次：事件会在后续每个询问的窗口里重复出现。
        # 行为类带观察者相关的后缀（``_me``），故按观察者各计一次。
        seen: set[tuple[int, int, int]] = set()
        last_views: dict[int, Any] = {}
        for viewer, view in sink:
            last_views[viewer] = view
            viewer_identity = truth.get(viewer)
            if viewer_identity is None:  # pragma: no cover - 身份局里每座都有身份
                continue
            for observation in identity.behavior_observations(view):
                key = (viewer, observation.seq, observation.target)
                if key in seen:
                    continue
                seen.add(key)
                actor_label = truth.get(observation.actor)
                if actor_label is None:  # pragma: no cover - 身份局里每座都有身份
                    continue
                counts.add(
                    actor_label.value,
                    viewer_identity.value,
                    observation.behavior,
                    observation.context,
                )
        # 长期证据（public_counts）：每局每个观察者取**最后一次**视图，计数是全局长。
        for viewer, view in last_views.items():
            viewer_identity = truth.get(viewer)
            if viewer_identity is None:  # pragma: no cover
                continue
            for pressure in identity.pressure_observations(view):
                actor_label = truth.get(pressure.actor)
                if actor_label is None:  # pragma: no cover
                    continue
                counts.add_pressure(
                    actor_label.value,
                    viewer_identity.value,
                    pressure.behavior,
                    pressure.count,
                )
        counts.games += 1
        if progress is not None:
            progress(offset + 1, games)
    return counts


def estimate(counts: Counts, *, alpha: float = DEFAULT_ALPHA) -> Estimate:
    """按 ``(count + α) / (行分母 + α × 类数)`` 平滑，产出 ``(行为, 先手方, 观察者)`` 表。

    分母 = **同一（发起身份, 观察者身份, 先手方）在九个行为类上的观测数**：于是同一行上的
    九个行为概率求和为 1——它们是一个条件分布，不是九条独立读数。分母为 0 ⇒ 均分。
    """
    if alpha <= 0:
        raise SystemExit(f"alpha 必须为正，得到 {alpha}")
    classes = len(identity.BEHAVIOR_CLASSES)
    table: dict[tuple[str, str, str], tuple[float, float, float, float]] = {}
    for context in identity.CONTEXTS:
        for behavior in identity.BEHAVIOR_CLASSES:
            for viewer in identity.IDENTITY_ORDER:
                values = []
                for actor in identity.IDENTITY_ORDER:
                    count = counts.counts.get(
                        (actor.value, viewer.value, behavior, context), 0
                    )
                    total = counts.row_total(actor.value, viewer.value, context)
                    if total == 0:
                        values.append(1.0 / classes)
                    else:
                        values.append((count + alpha) / (total + alpha * classes))
                table[(behavior, context, viewer.value)] = tuple(values)
    pressure_table: dict[tuple[str, str], tuple[float, float, float, float]] = {}
    pressure_classes = len(identity.PRESSURE_CLASSES)
    for behavior in identity.PRESSURE_CLASSES:
        for viewer in identity.IDENTITY_ORDER:
            values = []
            for actor in identity.IDENTITY_ORDER:
                count = counts.pressures.get((actor.value, viewer.value, behavior), 0)
                total = counts.pressure_row_total(actor.value, viewer.value)
                if total == 0:
                    values.append(1.0 / pressure_classes)
                else:
                    values.append(
                        (count + alpha) / (total + alpha * pressure_classes)
                    )
            pressure_table[(behavior, viewer.value)] = tuple(values)
    return Estimate(table=table, pressure_table=pressure_table, counts=counts)


def _float(value: float) -> str:
    """表里的数写成 6 位小数（可复算；票面的差能逐位核）。"""
    return f"{value:.6f}"


def render_source(estimate_obj: Estimate) -> str:
    """把表渲染成 ``BEHAVIOR_LIKELIHOODS`` 源码片段（可直接整块替换）。"""
    counts = estimate_obj.counts
    versions = " / ".join(ref for ref, _commit, _digest in counts.versions)
    lines = [
        "BEHAVIOR_LIKELIHOODS: Mapping[",
        "    tuple[str, str, str], tuple[float, float, float, float]",
        "] = MappingProxyType(",
        "    {",
        f"        # 由 tools/sgs_train/likelihood.py 估计：{counts.games} 局 / "
        f"{counts.observations} 条观测 / 版本混合 {versions}",
        "        # 每键 = P(行为类 | 发起身份, 观察者身份, 先手方)；"
        "值按 IDENTITY_ORDER（发起）",
    ]
    for context in identity.CONTEXTS:
        for behavior in identity.BEHAVIOR_CLASSES:
            for viewer in identity.IDENTITY_ORDER:
                values = estimate_obj.table[(behavior, context, viewer.value)]
                rendered = ", ".join(_float(value) for value in values)
                totals = " / ".join(
                    str(counts.row_total(actor.value, viewer.value, context))
                    for actor in identity.IDENTITY_ORDER
                )
                lines.append(
                    f'            ("{behavior}", "{context}", "{viewer.value}"): '
                    f"({rendered}),  # 分母 {totals}"
                )
    lines.extend(["    }", ")"])
    lines.extend(
        [
            "",
            "PRESSURE_LIKELIHOODS: Mapping[",
            "    tuple[str, str], tuple[float, float, float, float]",
            "] = MappingProxyType(",
            "    {",
        ]
    )
    for behavior in identity.PRESSURE_CLASSES:
        for viewer in identity.IDENTITY_ORDER:
            values = estimate_obj.pressure_table[(behavior, viewer.value)]
            rendered = ", ".join(_float(value) for value in values)
            totals = " / ".join(
                str(counts.pressure_row_total(actor.value, viewer.value))
                for actor in identity.IDENTITY_ORDER
            )
            lines.append(
                f'        ("{behavior}", "{viewer.value}"): ({rendered}),'
                f"  # 分母 {totals}"
            )
    lines.extend(["    }", ")"])
    return "\n".join(lines)


def _summary(estimate_obj: Estimate) -> list[str]:
    counts = estimate_obj.counts
    rows = [
        f"games={counts.games} observations={counts.observations} "
        f"skipped={len(counts.skipped)}",
        "versions="
        + " ".join(f"{ref}@{commit[:12]}" for ref, commit, _ in counts.versions),
    ]
    for context in identity.CONTEXTS:
        for viewer in identity.IDENTITY_ORDER:
            totals = " ".join(
                f"{actor.value}={counts.row_total(actor.value, viewer.value, context)}"
                for actor in identity.IDENTITY_ORDER
            )
            rows.append(f"{context}/{viewer.value}: {totals}")
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="估计身份行为似然表（ai-view 票 07；多版本混合 AI 对局）"
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        required=True,
        help="轮换的启发式 ref（至少两个；从 git tag 加载，不读工作树）",
    )
    parser.add_argument("--games", type=int, default=120, help="对局数（缺省 120）")
    parser.add_argument("--players", type=int, default=PLAYERS, help="座位数（缺省 5）")
    parser.add_argument(
        "--seed-base", type=int, default=910_000, help="种子起始（逐局 +1）"
    )
    parser.add_argument(
        "--deck-profile", default=DECK_PROFILE, help=f"牌堆档位（缺省 {DECK_PROFILE}）"
    )
    parser.add_argument(
        "--alpha", type=float, default=DEFAULT_ALPHA, help="平滑系数（缺省 0.5）"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="把含样本量 / 版本自证的 JSON 写到这里（stdout 只放源码片段）",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="不打印进度（摘要与源码照打）"
    )
    args = parser.parse_args(argv)

    def progress(done: int, total: int) -> None:
        if not args.quiet and (done % 10 == 0 or done == total):
            print(f"  {done}/{total}", file=sys.stderr)

    counts = collect_counts(
        versions=args.versions,
        games=args.games,
        seed_base=args.seed_base,
        players=args.players,
        deck_profile=args.deck_profile,
        progress=progress,
    )
    estimate_obj = estimate(counts, alpha=args.alpha)
    for line in _summary(estimate_obj):
        print(line, file=sys.stderr)
    if args.out is not None:
        payload = estimate_obj.to_dict()
        payload["alpha"] = args.alpha
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"JSON 写到 {args.out}", file=sys.stderr)
    print(render_source(estimate_obj))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
