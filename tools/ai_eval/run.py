"""三档命令的数据采集与结果装配（票 13；spec §2.5 / §2.8 / §2.9）。

**赛制固定**（spec §2.5）：5 人身份局；每副牌**换座重打**——A 局候选坐 0 号、B 局基线坐
0 号，其余四席是同一个对手（本实现取**基线**当那四个对手，故 A/A 的配对差恰为 0）。
其余四席用同一份代码 + 各自的 ``ai:<seat>`` 子流，所以两臂在第一个不同步的应答之前逐条
相同（``decisions.diff_decisions`` 的对齐前提）。

三个档位只差"跑哪几臂"：

- ``quick``：A / B 两臂，只出决策 diff（胜负字段写 ``null``）；
- ``promote``：再加"对原点基线"的 C / D 两臂（C 局候选坐 0 号、D 局原点坐 0 号，对手
  都是原点），出两组配对差 + 决策 diff + 分身份 + 五条件字段；
- ``seal``：每个版本各跑一臂**自身四副本**（不换座），只出聚合，``versions[]`` 逐版记读数。

**内存**：``sgs_batch`` 的 ``capture_decisions`` 会把每局几百条决策留在结果对象里，768 副
一次性拿回是几百 MB。故分块（:data:`CHUNK_SIZE`）跑完立刻折叠：

- 决策 diff 只需要**焦点座位**（0 号）的决策——其余四席是同一份代码同一批子流，在焦点座位
  第一个不同步之前逐条相同，所以"第一个不同步"必定落在焦点座位上；
- 漏动作率需要**全部座位**的决策，故每块立刻折算成计数，不保留明细。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sgs.data import load_content_pack
from sgs.engine import ai_stream
from sgs_batch import GameSpec, run_games

from .cases import CasesResult, run_cases
from .decisions import diff_decisions
from .errors import AiEvalSpecError
from .misses import MissCounts, count_misses, fact_from_batch_decision
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

EXAMPLE_LIMIT = 5
"""``decision_diff.examples`` 的条数（人读用；全量逐副可从种子复算）。"""

Progress = Callable[[str, int, int], None]
"""``progress(标签, 已完成, 总数)``：CLI 打到 stderr；库调用可省。"""

# ---------------------------------------------------------------------------
# 臂：每座位的版本 → 参与者；结果折成小对象
# ---------------------------------------------------------------------------


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

    @property
    def seeds(self) -> tuple[int, ...]:
        return tuple(game.seed for game in self.games)


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


def _environment(repo_root: Path | None, versions_dir: Path | None) -> tuple[Path, Path]:
    root = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
    versions = (
        Path(versions_dir) if versions_dir is not None else root / DEFAULT_VERSIONS_DIR
    ).resolve()
    return root, versions


def _focus_decisions(decisions: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(
        decision for decision in decisions if decision.get("actor") == FOCUS_SEAT
    )


def _arm_game(game: Any, *, keep_decisions: bool) -> ArmGame:
    """``GameResult`` → :class:`ArmGame`（未终局 / 报错的局 ``won=None``、退出分母）。"""
    decisions = (
        _focus_decisions(game.actions) if keep_decisions else ()
    )
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
    focus: str,
    table: str,
    root: Path,
    versions: Path,
    workers: int,
    capture: bool,
    keep_decisions: bool,
    progress: Progress | None = None,
    label: str = "",
) -> ArmRun:
    """跑完一个臂（分块）；每块结束回调一次 :data:`Progress`，决策明细当块折掉。"""
    factory = SeatVersions(
        focus=focus,
        table=table,
        repo_root=str(root),
        versions_dir=str(versions),
    )
    games: list[ArmGame] = []
    misses = 0
    pass_denominator = 0
    composites = 0
    composite_denominator = 0
    ordered = list(seeds)
    for start in range(0, len(ordered), CHUNK_SIZE):
        chunk = ordered[start : start + CHUNK_SIZE]
        batch = run_games(
            seeds=chunk,
            mode=MODE,
            players=PLAYERS,
            participants_factory=factory,
            capture_decisions=capture,
            workers=workers,
        )
        if capture:
            counts = count_misses(
                fact_from_batch_decision(decision)
                for game in batch.games
                for decision in game.actions
            )
            misses += counts.pass_with_options
            pass_denominator += counts.pass_denominator
            composites += counts.unfilled_composite
            composite_denominator += counts.composite_denominator
        games.extend(
            _arm_game(game, keep_decisions=keep_decisions) for game in batch.games
        )
        if progress is not None:
            progress(label, len(games), len(ordered))
    return ArmRun(
        label=label,
        games=tuple(games),
        miss=MissCounts(
            pass_with_options=misses,
            pass_denominator=pass_denominator,
            unfilled_composite=composites,
            composite_denominator=composite_denominator,
        ),
    )


def _errors(arms: Sequence[ArmRun]) -> int:
    return sum(1 for arm in arms for game in arm.games if not game.finished)


def _pairs(
    candidate: ArmRun, baseline: ArmRun
) -> list[tuple[str | None, bool | None, bool | None]]:
    """逐副配对：两臂都打完才进分母（一臂报错整副退出，不拿半副凑数）。"""
    baseline_by_seed = {game.seed: game for game in baseline.games}
    pairs: list[tuple[str | None, bool | None, bool | None]] = []
    for game in candidate.games:
        twin = baseline_by_seed.get(game.seed)
        identity = game.identity
        if identity is None and twin is not None:
            identity = twin.identity
        pairs.append(
            (
                identity,
                game.won if game.finished else None,
                twin.won if twin is not None and twin.finished else None,
            )
        )
    return pairs


def _decision_diff(
    candidate: ArmRun,
    baseline: ArmRun,
    *,
    limit: int = EXAMPLE_LIMIT,
) -> dict[str, Any]:
    """两臂逐副比焦点座位的决策：第一个不同步的询问类型归因 + 前 ``limit`` 条例子。"""
    baseline_by_seed = {game.seed: game for game in baseline.games}
    differing = 0
    kinds: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    for game in candidate.games:
        twin = baseline_by_seed.get(game.seed)
        if twin is None or not game.finished or not twin.finished:
            continue
        divergence = diff_decisions(game.decisions, twin.decisions)
        if divergence is None:
            continue
        differing += 1
        kinds[divergence.kind] += 1
        if len(examples) < limit:
            examples.append(
                {
                    "seed": game.seed,
                    "index": divergence.index,
                    "kind": divergence.kind,
                    "actor": divergence.actor,
                    "candidate": divergence.candidate_action,
                    "baseline": divergence.baseline_action,
                }
            )
    return {
        "deals_differing": differing,
        "first_divergence_by_kind": dict(sorted(kinds.items())),
        "examples": examples,
    }


def _verdict(
    *,
    cases: CasesResult,
    non_inferior: bool | None,
    anchor_non_inferior: bool | None,
    first_divergence_by_kind: dict[str, int] | None,
    predicted_kinds: Sequence[str],
) -> dict[str, bool | None]:
    attributed: bool | None = None
    if predicted_kinds and first_divergence_by_kind is not None:
        attributed = set(first_divergence_by_kind) <= set(predicted_kinds)
    return {
        "cases_green": cases.green,
        "non_inferior": non_inferior,
        "anchor_non_inferior": anchor_non_inferior,
        "diff_attributed": attributed,
        "human_decision": None,
    }


def _absolute(arm: ArmRun) -> dict[str, Any]:
    """``seal`` 的逐版本绝对读数：胜率 + bootstrap CI + 分身份（不换座、无配对差）。"""
    finished = [game for game in arm.games if game.finished]
    wins = [1.0 if game.won else 0.0 for game in finished]
    if wins:
        win_rate = round(sum(wins) / len(wins), 4)
        low, high = bootstrap_mean_ci(wins)
        ci95 = [round(low, 4), round(high, 4)]
    else:
        win_rate = 0.0
        ci95 = [0.0, 0.0]
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


def _side(loaded: LoadedVersion | None) -> dict[str, str] | None:
    if loaded is None:
        return None
    return {"ref": loaded.commit, "heuristic_sha256": loaded.heuristic_sha256}


def _ref_side(loaded: LoadedVersion | None) -> dict[str, str] | None:
    if loaded is None:
        return None
    return {"ref": loaded.ref, "heuristic_sha256": loaded.heuristic_sha256}


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
    versions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """§2.8 的结果对象（键齐备；该档测不到的字段写 ``null``）。"""
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "tier": tier,
        "candidate": _side(candidate),
        "baseline": _ref_side(baseline),
        "anchor": _ref_side(anchor),
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
    }
    if versions is not None:
        payload["versions"] = versions
    return payload


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
    root, versions = _environment(repo_root, versions_dir)
    candidate = load_version(candidate_ref, repo_root=root, versions_dir=versions)
    baseline_version = load_version(baseline, repo_root=root, versions_dir=versions)
    seed_batch = derive_batch(candidate.commit, deals)
    arm_a = _run_arm(
        seed_batch.seeds,
        focus=candidate_ref,
        table=baseline,
        root=root,
        versions=versions,
        workers=workers,
        capture=True,
        keep_decisions=True,
        progress=progress,
        label="quick/candidate",
    )
    arm_b = _run_arm(
        seed_batch.seeds,
        focus=baseline,
        table=baseline,
        root=root,
        versions=versions,
        workers=workers,
        capture=True,
        keep_decisions=True,
        progress=progress,
        label="quick/baseline",
    )
    diff = _decision_diff(arm_a, arm_b)
    cases = run_cases(root) if measure_cases else CasesResult()
    return _result(
        tier="quick",
        candidate=candidate,
        baseline=baseline_version,
        anchor=None,
        engine_ref=git_commit("HEAD", repo_root=root),
        deck_profile=load_content_pack().deck_profile,
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
            predicted_kinds=predicted_kinds,
        ),
        errors=_errors([arm_a, arm_b]),
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
    root, versions = _environment(repo_root, versions_dir)
    candidate = load_version(candidate_ref, repo_root=root, versions_dir=versions)
    baseline_version = load_version(baseline, repo_root=root, versions_dir=versions)
    anchor_version = load_version(anchor, repo_root=root, versions_dir=versions)
    seed_batch = derive_batch(candidate.commit, deals)
    seeds = seed_batch.seeds
    candidate_vs_baseline = _run_arm(
        seeds,
        focus=candidate_ref,
        table=baseline,
        root=root,
        versions=versions,
        workers=workers,
        capture=True,
        keep_decisions=True,
        progress=progress,
        label="promote/vs-baseline/candidate",
    )
    baseline_vs_baseline = _run_arm(
        seeds,
        focus=baseline,
        table=baseline,
        root=root,
        versions=versions,
        workers=workers,
        capture=True,
        keep_decisions=True,
        progress=progress,
        label="promote/vs-baseline/baseline",
    )
    # 对原点的两臂不保留决策（不需要 diff），只出胜负与分身份。
    candidate_vs_anchor = _run_arm(
        seeds,
        focus=candidate_ref,
        table=anchor,
        root=root,
        versions=versions,
        workers=workers,
        capture=False,
        keep_decisions=False,
        progress=progress,
        label="promote/vs-anchor/candidate",
    )
    anchor_vs_anchor = _run_arm(
        seeds,
        focus=anchor,
        table=anchor,
        root=root,
        versions=versions,
        workers=workers,
        capture=False,
        keep_decisions=False,
        progress=progress,
        label="promote/vs-anchor/anchor",
    )
    vs_baseline = compare_pairs(_pairs(candidate_vs_baseline, baseline_vs_baseline))
    vs_anchor = compare_pairs(_pairs(candidate_vs_anchor, anchor_vs_anchor))
    delta = noise_floor(deals)
    diff = _decision_diff(candidate_vs_baseline, baseline_vs_baseline)
    cases = run_cases(root) if measure_cases else CasesResult()
    return _result(
        tier="promote",
        candidate=candidate,
        baseline=baseline_version,
        anchor=anchor_version,
        engine_ref=git_commit("HEAD", repo_root=root),
        deck_profile=load_content_pack().deck_profile,
        seed_batch=seed_batch,
        duplicate_play=True,
        vs_baseline=vs_baseline,
        vs_anchor=vs_anchor,
        decision_diff=diff,
        cases=cases,
        miss_rate=candidate_vs_baseline.miss.to_dict(),
        verdict=_verdict(
            cases=cases,
            non_inferior=vs_baseline.ci95[0] >= -delta,
            anchor_non_inferior=vs_anchor.ci95[0] >= -delta,
            first_divergence_by_kind=diff["first_divergence_by_kind"],
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
    )


def run_seal(
    *,
    batch: str,
    versions: Sequence[str],
    deals: int = 1024,
    workers: int = 1,
    repo_root: Path | None = None,
    versions_dir: Path | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """封印：固定 1024 副、每个版本各跑一臂自身四副本，**只出聚合**。"""
    if not versions:
        raise AiEvalSpecError("seal 需要至少一个 --versions（例如 ai-v0 ai-v1）")
    if len(set(versions)) != len(versions):
        raise AiEvalSpecError(f"seal 的 --versions 不能重复：{list(versions)}")
    root, resolved_versions = _environment(repo_root, versions_dir)
    loaded = [
        load_version(ref, repo_root=root, versions_dir=resolved_versions)
        for ref in versions
    ]
    seed_batch = derive_sealed_batch(batch, deals)
    arms = [
        _run_arm(
            seed_batch.seeds,
            focus=ref,
            table=ref,
            root=root,
            versions=resolved_versions,
            workers=workers,
            capture=True,
            keep_decisions=False,
            progress=progress,
            label=f"seal/{ref}",
        )
        for ref in versions
    ]
    entries = [_version_entry(version, arm) for version, arm in zip(loaded, arms)]
    combined = MissCounts(
        pass_with_options=sum(arm.miss.pass_with_options for arm in arms),
        pass_denominator=sum(arm.miss.pass_denominator for arm in arms),
        unfilled_composite=sum(arm.miss.unfilled_composite for arm in arms),
        composite_denominator=sum(arm.miss.composite_denominator for arm in arms),
    )
    return _result(
        tier="seal",
        candidate=None,
        baseline=None,
        anchor=None,
        engine_ref=git_commit("HEAD", repo_root=root),
        deck_profile=load_content_pack().deck_profile,
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
            predicted_kinds=(),
        ),
        errors=_errors(arms),
        versions=entries,
    )
