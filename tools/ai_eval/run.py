"""三档命令的数据采集与结果装配（票 13；spec §2.5 / §2.8 / §2.9）。

**赛制固定**（spec §2.5）：5 人身份局；每副牌**换座重打**——A 局候选坐 0 号、B 局基线坐
0 号，其余四席是同一个对手（本实现取**基线**当那四个对手，故 A/A 的配对差恰为 0）。
其余四席用同一份代码 + 各自的 ``ai:<seat>`` 子流，所以两臂在第一个不同步的应答之前逐条
相同（``decisions.diff_decisions`` 的对齐前提）。

三个档位只差"跑哪几臂"：

- ``quick``：A / B 两臂，只出决策 diff（胜负字段写 ``null``）；
- ``promote``：再加"对原点基线"的 C / D 两臂（C 局候选坐 0 号、D 局原点坐 0 号，对手
  都是原点），出两组配对差 + 决策 diff + 分身份 + 五条件字段；
- ``seal``：每个版本各跑一臂**自身四副本**（不换座），只出聚合，``versions[]`` 逐版记读数；
  副数**写死 1024**（spec §2.4 B），CLI 不暴露副数（测试用库参数缩小）。

**内存**：``sgs_batch`` 的 ``capture_decisions`` 会把每局几百条决策留在结果对象里，768 副
一次性拿回是几百 MB。故分块（:data:`CHUNK_SIZE`）跑完立刻折叠：

- 决策 diff 只需要**焦点座位**（0 号）的决策——其余四席是同一份代码同一批子流，在焦点座位
  第一个不同步之前逐条相同，所以"第一个不同步"必定落在焦点座位上；
- 漏动作率需要**全部座位**的决策，故每块立刻折算成计数（分询问类型的明细一并合并），
  不保留明细。

**未知询问类型（票 36）**：旧版本的分发表可能缺它打 tag 之后才新增的 ``QueryKind``；
兼容层（:mod:`ai_eval.compat`）把这些询问换成**合法放弃**，驱动侧从决策侧车数
``capability_gaps``（``{ref: {kind: n}}``，跨进程不丢）。它**不进** ``errors``——
``errors`` 只数真正的崩溃。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sgs.data import load_content_pack
from sgs.engine import ai_stream
from sgs_batch import GameSpec, run_games

from .cases import CasesResult, run_cases
from .decisions import decision_diff
from .errors import AiEvalSpecError
from .misses import (
    MissCounts,
    count_misses,
    fact_from_batch_decision,
    merge_misses,
)
from .seeds import SeedBatch, derive_batch, derive_sealed_batch
from .stats import PairedComparison, bootstrap_mean_ci, compare_pairs, noise_floor
from .versions import (
    DEFAULT_VERSIONS_DIR,
    LoadedVersion,
    git_commit,
    load_version,
    repository_root,
)

__all__ = ["run_promote", "run_quick", "run_seal"]

SCHEMA = "sgs.ai_eval.v1"
"""结果 JSON 的 schema 字符串（spec §2.8 收口：与 ``sgs.batch.v1`` 同形）。"""

PLAYERS = 5
MODE = "identity"
FOCUS_SEAT = 0
"""焦点座位：候选 / 基线读数都数这个座位（spec §2.5 的"坐 0 号"）。"""

CHUNK_SIZE = 64
"""每块局数：``capture_decisions`` 的决策明细是内存大头，分块折叠（见模块 docstring）。"""

SEAL_DEALS = 1024
"""封印批的副数（spec §2.4 B：1024 副**写死**；CLI 不暴露这个数）。"""

Progress = Callable[[str, int, int], None]
"""``progress(标签, 已完成, 总数)``：CLI 打到 stderr；库调用可省。"""


# ---------------------------------------------------------------------------
# 环境与臂：每座位的版本 → 参与者；结果折成小对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Environment:
    """一次评估落地的三样事实：仓库根、版本副本目录、进程数。"""

    root: Path
    versions: Path
    workers: int

    @classmethod
    def resolve(
        cls,
        repo_root: Path | None,
        versions_dir: Path | None,
        workers: int,
    ) -> "Environment":
        root = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
        versions = (
            Path(versions_dir) if versions_dir is not None else root / DEFAULT_VERSIONS_DIR
        ).resolve()
        return cls(root=root, versions=versions, workers=workers)


@dataclass(frozen=True)
class ArmSpec:
    """一个臂的跑法：座位 0 用 ``focus``、其余四席用 ``table``。"""

    focus: str
    table: str
    label: str
    capture: bool = True
    """要不要记录决策侧车（漏动作率 / 决策 diff / 票 36 的 ``capability_gaps`` 都要）。

    三个档位现在都开着：``capability_gaps`` 从侧车的 ``kind`` 数（跨进程不丢），关掉它
    就没有第二个读数来源；``keep_decisions=False`` 只丢"焦点座位那几副"的明细，侧车
    仍然逐条折掉。
    """

    keep_decisions: bool = True
    """要不要留**焦点座位**的决策（``seal`` 只要聚合，故关掉）。"""


@dataclass(frozen=True)
class ArmGame:
    """一局里焦点座位的事实 + （可选）焦点座位的决策明细。"""

    seed: int
    identity: str | None
    won: bool | None
    finished: bool
    decisions: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ArmRun:
    """一个臂的读数：逐副事实 + 该臂全部座位的漏动作计数（spec §2.11）。"""

    label: str
    games: tuple[ArmGame, ...]
    miss: MissCounts
    capability_gaps: dict[str, dict[str, int]] = field(default_factory=dict)
    """``{ref: {kind: n}}``：这个臂里由**兼容层**放弃应答的次数（票 36）。

    只数"这个座位用的那个版本选路表里没有的类型"：分发表完整的版本（HEAD）不产生条目。
    驱动侧从这里汇总结果 JSON 的 ``capability_gaps``——它**不是** ``errors``。
    """


@dataclass(frozen=True)
class SeatVersions:
    """``participants_factory``：座位 0 用焦点版本，其余四席用对手版本。

    字段全是字符串 ⇒ 可 pickle；worker（``fork``）继承父进程的版本缓存，故每个版本在
    一个进程里只读一次 git、只 import 一次。
    """

    focus: str
    table: str
    repo_root: str
    versions_dir: str

    def __call__(self, spec: GameSpec) -> list[Any]:
        refs = [self.table] * spec.players
        refs[FOCUS_SEAT] = self.focus
        return [
            load_version(
                ref,
                repo_root=Path(self.repo_root),
                versions_dir=Path(self.versions_dir),
            ).player_class(ai_stream(spec.seed, seat))
            for seat, ref in enumerate(refs)
        ]


def _focus_decisions(decisions: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        decision for decision in decisions if decision.get("actor") == FOCUS_SEAT
    )


def _merge_capability_gaps(
    left: dict[str, dict[str, int]], right: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    """合并两块（批量分块跑时每块折一次）；``{ref: {kind: n}}`` 逐格相加。"""
    merged = {ref: dict(kinds) for ref, kinds in left.items()}
    for ref, kinds in right.items():
        bucket = merged.setdefault(ref, {})
        for kind, count in kinds.items():
            bucket[kind] = bucket.get(kind, 0) + count
    return merged


def _decision_capability_gaps(
    decisions: Iterable[dict[str, Any]],
    *,
    arm: ArmSpec,
    focus_gaps: frozenset[str],
    table_gaps: frozenset[str],
) -> dict[str, dict[str, int]]:
    """数"兼容层替旧版本放弃了哪几类询问"（票 36）：座位 → 它用的那个版本。

    决策侧车（``kind`` / ``actor``）不随进程丢，故 ``--workers 1..8`` 同口径；只计
    **选路表缺那一类的座位**——当前版本（HEAD）永远不进这个计数。
    """
    counts: dict[str, dict[str, int]] = {}
    for decision in decisions:
        if decision.get("actor") == FOCUS_SEAT:
            ref, gaps = arm.focus, focus_gaps
        else:
            ref, gaps = arm.table, table_gaps
        kind = decision.get("kind")
        if kind in gaps:
            bucket = counts.setdefault(ref, {})
            bucket[kind] = bucket.get(kind, 0) + 1
    return counts


def _arm_game(game: Any, *, keep_decisions: bool) -> ArmGame:
    """``GameResult`` → :class:`ArmGame`（未终局 / 报错的局 ``won=None``、退出分母）。"""
    decisions = _focus_decisions(game.actions) if keep_decisions else ()
    if game.error is not None or not game.finished or game.outcome is None or not game.seats:
        return ArmGame(
            seed=game.seed,
            identity=None,
            won=None,
            finished=False,
            decisions=decisions,
        )
    focus = game.seats[FOCUS_SEAT]
    return ArmGame(
        seed=game.seed,
        identity=focus.identity,
        won=bool(focus.won),
        finished=True,
        decisions=decisions,
    )


def _run_arm(
    seeds: Sequence[int],
    *,
    env: Environment,
    arm: ArmSpec,
    progress: Progress | None = None,
) -> ArmRun:
    """跑完一个臂（分块）；每块结束回调一次 :data:`Progress`，决策明细当块折掉。"""
    factory = SeatVersions(
        focus=arm.focus,
        table=arm.table,
        repo_root=str(env.root),
        versions_dir=str(env.versions),
    )
    focus_version = load_version(arm.focus, repo_root=env.root, versions_dir=env.versions)
    table_version = load_version(arm.table, repo_root=env.root, versions_dir=env.versions)
    focus_gaps = frozenset(focus_version.capability_gap_kinds)
    table_gaps = frozenset(table_version.capability_gap_kinds)
    if not arm.capture and (focus_gaps or table_gaps):
        raise AiEvalSpecError(
            f"{arm.label}：{arm.focus} / {arm.table} 有选路缺口但本臂不记决策明细 ⇒ 数不了"
            " capability_gaps（兼容层只换应答；要数缺口就打开 capture）"
        )
    games: list[ArmGame] = []
    miss = MissCounts(0, 0, 0, 0)
    gaps: dict[str, dict[str, int]] = {}
    ordered = list(seeds)
    for start in range(0, len(ordered), CHUNK_SIZE):
        chunk = ordered[start : start + CHUNK_SIZE]
        batch = run_games(
            seeds=chunk,
            mode=MODE,
            players=PLAYERS,
            participants_factory=factory,
            capture_decisions=arm.capture,
            workers=env.workers,
        )
        if arm.capture:
            miss = merge_misses(
                miss,
                count_misses(
                    fact_from_batch_decision(decision)
                    for game in batch.games
                    for decision in game.actions
                ),
            )
            gaps = _merge_capability_gaps(
                gaps,
                _decision_capability_gaps(
                    (
                        decision
                        for game in batch.games
                        for decision in game.actions
                    ),
                    arm=arm,
                    focus_gaps=focus_gaps,
                    table_gaps=table_gaps,
                ),
            )
        games.extend(
            _arm_game(game, keep_decisions=arm.keep_decisions) for game in batch.games
        )
        if progress is not None:
            progress(arm.label, len(games), len(ordered))
    return ArmRun(label=arm.label, games=tuple(games), miss=miss, capability_gaps=gaps)


def _errors(arms: Sequence[ArmRun]) -> int:
    """``errors``：**没打完**的局数（崩溃 / 未终局）。

    兼容层的合法放弃发生在 ``errors`` 之前（旧版本仍把这一副打完），票 36 起它记
    ``capability_gaps``、不进这里——``errors`` 只数真正的崩溃。
    """
    return sum(1 for arm in arms for game in arm.games if not game.finished)


def _finished_pairs(
    candidate: ArmRun, baseline: ArmRun
) -> list[tuple[int, ArmGame, ArmGame]]:
    """两臂都打完的逐副配对（一臂报错整副退出，不拿半副凑数）。"""
    baseline_by_seed = {game.seed: game for game in baseline.games}
    pairs = []
    for game in candidate.games:
        twin = baseline_by_seed.get(game.seed)
        if twin is None or not game.finished or not twin.finished:
            continue
        pairs.append((game.seed, game, twin))
    return pairs


def _pairs(
    candidate: ArmRun, baseline: ArmRun
) -> list[tuple[str | None, bool | None, bool | None]]:
    """逐副 ``(身份, 候选赢?, 基线赢?)``；身份取候选臂（A/A 时两臂相同）。"""
    pairs: list[tuple[str | None, bool | None, bool | None]] = []
    for _, game, twin in _finished_pairs(candidate, baseline):
        identity = game.identity if game.identity is not None else twin.identity
        pairs.append((identity, game.won, twin.won))
    return pairs


def _decision_diff(candidate: ArmRun, baseline: ArmRun) -> dict[str, Any]:
    """两臂逐副的焦点座位决策 diff（§2.8 的 ``decision_diff``）。"""
    return decision_diff(
        [
            (seed, game.decisions, twin.decisions)
            for seed, game, twin in _finished_pairs(candidate, baseline)
        ]
    )


def _verdict(
    *,
    cases: CasesResult,
    non_inferior: bool | None,
    anchor_non_inferior: bool | None,
    first_divergence_by_kind: dict[str, int] | None,
    matched_deals: int,
    predicted_kinds: Sequence[str],
) -> dict[str, bool | None]:
    """五条件字段；**没有一副有效配对**时 diff 归因写 ``null``（未测 ≠ 通过）。"""
    attributed: bool | None = None
    if predicted_kinds and first_divergence_by_kind is not None and matched_deals > 0:
        attributed = set(first_divergence_by_kind) <= set(predicted_kinds)
    return {
        "cases_green": cases.green,
        "non_inferior": non_inferior,
        "anchor_non_inferior": anchor_non_inferior,
        "diff_attributed": attributed,
        "human_decision": None,
    }


def _absolute(arm: ArmRun) -> dict[str, Any]:
    """``seal`` 的逐版本绝对读数：胜率 + bootstrap CI + 分身份（不换座、无配对差）。

    一副打完的牌都没有 ⇒ 胜率 / CI 写 ``None``（未测 ≠ 0%），与判定字段的 NULL 语义一致。
    """
    finished = [game for game in arm.games if game.finished]
    if finished:
        wins = [1.0 if game.won else 0.0 for game in finished]
        win_rate: float | None = round(sum(wins) / len(wins), 4)
        low, high = bootstrap_mean_ci(wins)
        ci95: list[float] | None = [round(low, 4), round(high, 4)]
    else:
        win_rate = None
        ci95 = None
    grouped: dict[str, list[float]] = {}
    for game in finished:
        if game.identity is None:
            continue
        grouped.setdefault(game.identity, []).append(1.0 if game.won else 0.0)
    by_identity = {
        identity: {
            "deals": len(values),
            "wins": int(sum(values)),
            "win_rate": round(sum(values) / len(values), 4),
        }
        for identity, values in grouped.items()
    }
    return {
        "deals": len(finished),
        "win_rate": win_rate,
        "ci95": ci95,
        "by_identity": by_identity,
    }


def _version_entry(loaded: LoadedVersion, arm: ArmRun) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ref": loaded.ref,
        "commit": loaded.commit,
        "heuristic_sha256": loaded.heuristic_sha256,
    }
    entry.update(_absolute(arm))
    entry["miss_rate"] = arm.miss.to_dict()
    return entry


def _side(loaded: LoadedVersion | None, *, by_commit: bool) -> dict[str, str] | None:
    """§2.8 的 ``{ref, heuristic_sha256}``。

    ``by_commit=True``（候选）写**解析后的提交 sha**——种子批的派生输入就是它；基线 / 原点
    写用户给的 tag 名（``ai-vN``），因为那是台账里认的版本标识。
    """
    if loaded is None:
        return None
    return {
        "ref": loaded.commit if by_commit else loaded.ref,
        "heuristic_sha256": loaded.heuristic_sha256,
    }


def _capability_gaps(
    arms: Sequence[ArmRun], versions: dict[str, LoadedVersion]
) -> dict[str, Any]:
    """§2.8 的 ``capability_gaps``（票 36）：按版本列的静态缺口 + 本次实际放弃次数。

    ``queries`` 跨臂累加（同一版本在不同臂里既可能是焦点座位、也可能是对手四席）；
    ``total`` = 全部版本的全部放弃次数。它**不进** ``errors``、也不影响退出码——
    它说明的是"基线们这次被兼容层替答了几次"，读 diff / 胜率时必须知道。
    """
    by_version: dict[str, dict[str, Any]] = {
        ref: {"kinds": list(version.capability_gap_kinds), "queries": {}}
        for ref, version in versions.items()
    }
    for arm in arms:
        for ref, kinds in arm.capability_gaps.items():
            entry = by_version.setdefault(ref, {"kinds": [], "queries": {}})
            bucket = entry["queries"]
            for kind, count in kinds.items():
                bucket[kind] = bucket.get(kind, 0) + count
    total = sum(sum(entry["queries"].values()) for entry in by_version.values())
    return {"policy": "legal_decline", "total": total, "by_version": by_version}


def _result(
    *,
    tier: str,
    candidate: LoadedVersion | None,
    baseline: LoadedVersion | None,
    anchor: LoadedVersion | None,
    engine_ref: str,
    deck_profile: str,
    seed_batch: SeedBatch,
    duplicate_play: bool,
    vs_baseline: PairedComparison | None,
    vs_anchor: PairedComparison | None,
    decision_diff: dict[str, Any] | None,
    cases: CasesResult,
    miss_rate: dict[str, float | None],
    verdict: dict[str, bool | None],
    errors: int,
    capability_gaps: dict[str, Any],
    versions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """§2.8 的结果对象（键齐备；该档测不到的字段写 ``null``）。"""
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "tier": tier,
        "candidate": _side(candidate, by_commit=True),
        "baseline": _side(baseline, by_commit=False),
        "anchor": _side(anchor, by_commit=False),
        "engine_ref": engine_ref,
        "deck_profile": deck_profile,
        "players": PLAYERS,
        "mode": MODE,
        "seed_batch": seed_batch.to_dict(),
        "design": {"duplicate_play": duplicate_play, "per_seat_streams": "ai:<seat>"},
        "vs_baseline": None if vs_baseline is None else vs_baseline.to_dict(),
        "vs_anchor": None if vs_anchor is None else vs_anchor.anchor_view(),
        "decision_diff": decision_diff,
        "cases": cases.to_dict(),
        "miss_rate": dict(miss_rate),
        "verdict": dict(verdict),
        "errors": errors,
        "capability_gaps": capability_gaps,
    }
    if versions is not None:
        payload["versions"] = versions
    return payload


def _engine_ref(env: Environment) -> str:
    return git_commit("HEAD", repo_root=env.root)


def _deck_profile() -> str:
    return load_content_pack().deck_profile


# ---------------------------------------------------------------------------
# 三档命令
# ---------------------------------------------------------------------------


def run_quick(
    *,
    baseline: str,
    candidate_ref: str = "HEAD",
    deals: int = 128,
    workers: int = 1,
    repo_root: Path | None = None,
    versions_dir: Path | None = None,
    measure_cases: bool = True,
    predicted_kinds: Sequence[str] = (),
    progress: Progress | None = None,
) -> dict[str, Any]:
    """快检：commit 派生批上的决策 diff（不看胜负；spec §2.9 的第一档）。"""
    env = Environment.resolve(repo_root, versions_dir, workers)
    candidate = load_version(candidate_ref, repo_root=env.root, versions_dir=env.versions)
    baseline_version = load_version(
        baseline, repo_root=env.root, versions_dir=env.versions
    )
    seed_batch = derive_batch(candidate.commit, deals)
    arm_a = _run_arm(
        seed_batch.seeds,
        env=env,
        arm=ArmSpec(focus=candidate_ref, table=baseline, label="quick/candidate"),
        progress=progress,
    )
    arm_b = _run_arm(
        seed_batch.seeds,
        env=env,
        arm=ArmSpec(focus=baseline, table=baseline, label="quick/baseline"),
        progress=progress,
    )
    diff = _decision_diff(arm_a, arm_b)
    matched = len(_finished_pairs(arm_a, arm_b))
    cases = run_cases(env.root) if measure_cases else CasesResult()
    return _result(
        tier="quick",
        candidate=candidate,
        baseline=baseline_version,
        anchor=None,
        engine_ref=_engine_ref(env),
        deck_profile=_deck_profile(),
        seed_batch=seed_batch,
        duplicate_play=True,
        vs_baseline=None,
        vs_anchor=None,
        decision_diff=diff,
        cases=cases,
        miss_rate=arm_a.miss.to_dict(),
        verdict=_verdict(
            cases=cases,
            non_inferior=None,
            anchor_non_inferior=None,
            first_divergence_by_kind=diff["first_divergence_by_kind"],
            matched_deals=matched,
            predicted_kinds=predicted_kinds,
        ),
        errors=_errors([arm_a, arm_b]),
        capability_gaps=_capability_gaps(
            [arm_a, arm_b],
            {candidate_ref: candidate, baseline: baseline_version},
        ),
    )


def run_promote(
    *,
    baseline: str,
    anchor: str,
    candidate_ref: str = "HEAD",
    deals: int = 768,
    workers: int = 1,
    repo_root: Path | None = None,
    versions_dir: Path | None = None,
    measure_cases: bool = True,
    predicted_kinds: Sequence[str] = (),
    progress: Progress | None = None,
) -> dict[str, Any]:
    """晋级：768 副换座配对 ×（父版本 + 原点基线）+ diff + 分身份 + 五条件。"""
    env = Environment.resolve(repo_root, versions_dir, workers)
    candidate = load_version(candidate_ref, repo_root=env.root, versions_dir=env.versions)
    baseline_version = load_version(
        baseline, repo_root=env.root, versions_dir=env.versions
    )
    anchor_version = load_version(anchor, repo_root=env.root, versions_dir=env.versions)
    seed_batch = derive_batch(candidate.commit, deals)
    seeds = seed_batch.seeds
    candidate_vs_baseline = _run_arm(
        seeds,
        env=env,
        arm=ArmSpec(
            focus=candidate_ref,
            table=baseline,
            label="promote/vs-baseline/candidate",
        ),
        progress=progress,
    )
    baseline_vs_baseline = _run_arm(
        seeds,
        env=env,
        arm=ArmSpec(
            focus=baseline,
            table=baseline,
            label="promote/vs-baseline/baseline",
        ),
        progress=progress,
    )
    # 对原点的两臂不**留**决策明细（不需要 diff），但照常**采集**侧车：票 36 的
    # capability_gaps 从决策侧车数（旧版本被兼容层替答了几次），不采集就数不出来。
    candidate_vs_anchor = _run_arm(
        seeds,
        env=env,
        arm=ArmSpec(
            focus=candidate_ref,
            table=anchor,
            label="promote/vs-anchor/candidate",
            keep_decisions=False,
        ),
        progress=progress,
    )
    anchor_vs_anchor = _run_arm(
        seeds,
        env=env,
        arm=ArmSpec(
            focus=anchor,
            table=anchor,
            label="promote/vs-anchor/anchor",
            keep_decisions=False,
        ),
        progress=progress,
    )
    vs_baseline = compare_pairs(_pairs(candidate_vs_baseline, baseline_vs_baseline))
    vs_anchor = compare_pairs(_pairs(candidate_vs_anchor, anchor_vs_anchor))
    # δ 用**预注册 N**（不是实际配对数）：阈值不能随"有多少副打完了"漂移。
    delta = noise_floor(deals)
    diff = _decision_diff(candidate_vs_baseline, baseline_vs_baseline)
    cases = run_cases(env.root) if measure_cases else CasesResult()
    return _result(
        tier="promote",
        candidate=candidate,
        baseline=baseline_version,
        anchor=anchor_version,
        engine_ref=_engine_ref(env),
        deck_profile=_deck_profile(),
        seed_batch=seed_batch,
        duplicate_play=True,
        vs_baseline=vs_baseline,
        vs_anchor=vs_anchor,
        decision_diff=diff,
        cases=cases,
        miss_rate=candidate_vs_baseline.miss.to_dict(),
        verdict=_verdict(
            cases=cases,
            non_inferior=(
                None
                if not vs_baseline.measured
                else vs_baseline.ci95[0] >= -delta
            ),
            anchor_non_inferior=(
                None if not vs_anchor.measured else vs_anchor.ci95[0] >= -delta
            ),
            first_divergence_by_kind=diff["first_divergence_by_kind"],
            matched_deals=vs_baseline.deals,
            predicted_kinds=predicted_kinds,
        ),
        errors=_errors(
            [
                candidate_vs_baseline,
                baseline_vs_baseline,
                candidate_vs_anchor,
                anchor_vs_anchor,
            ]
        ),
        capability_gaps=_capability_gaps(
            [
                candidate_vs_baseline,
                baseline_vs_baseline,
                candidate_vs_anchor,
                anchor_vs_anchor,
            ],
            {
                candidate_ref: candidate,
                baseline: baseline_version,
                anchor: anchor_version,
            },
        ),
    )


def run_seal(
    *,
    batch: str,
    versions: Sequence[str],
    workers: int = 1,
    deals: int | None = None,
    repo_root: Path | None = None,
    versions_dir: Path | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """封印：**1024 副写死**、每个版本各跑一臂自身四副本，只出聚合。

    ``deals`` 只给测试缩小批（CLI 不暴露）；缺省 :data:`SEAL_DEALS`。
    """
    if not versions:
        raise AiEvalSpecError("seal 需要至少一个 --versions（例如 ai-v0 ai-v1）")
    if len(set(versions)) != len(versions):
        raise AiEvalSpecError(f"seal 的 --versions 不能重复：{list(versions)}")
    env = Environment.resolve(repo_root, versions_dir, workers)
    loaded = [
        load_version(ref, repo_root=env.root, versions_dir=env.versions)
        for ref in versions
    ]
    seed_batch = derive_sealed_batch(batch, SEAL_DEALS if deals is None else deals)
    arms = [
        _run_arm(
            seed_batch.seeds,
            env=env,
            arm=ArmSpec(
                focus=ref,
                table=ref,
                label=f"seal/{ref}",
                keep_decisions=False,
            ),
            progress=progress,
        )
        for ref in versions
    ]
    entries = [_version_entry(version, arm) for version, arm in zip(loaded, arms)]
    combined = MissCounts(0, 0, 0, 0)
    for arm in arms:
        combined = merge_misses(combined, arm.miss)
    return _result(
        tier="seal",
        candidate=None,
        baseline=None,
        anchor=None,
        engine_ref=_engine_ref(env),
        deck_profile=_deck_profile(),
        seed_batch=seed_batch,
        duplicate_play=False,
        vs_baseline=None,
        vs_anchor=None,
        decision_diff=None,
        cases=CasesResult(),
        miss_rate=combined.to_dict(),
        verdict=_verdict(
            cases=CasesResult(),
            non_inferior=None,
            anchor_non_inferior=None,
            first_divergence_by_kind=None,
            matched_deals=0,
            predicted_kinds=(),
        ),
        errors=_errors(arms),
        capability_gaps=_capability_gaps(
            arms, {ref: version for ref, version in zip(versions, loaded)}
        ),
        versions=entries,
    )
