"""M2 基准的种子搜索：从零搜出 27 条基准的 ``(n, seed)``（票 32）。

**测试侧开发工具，不进 pytest**：搜索本身要跑上千局。它不做门禁断言、不参与回放，
只把候选搜出来打印，并核对 ``scenarios.py`` 写死的值是否仍然有效。录制期的唯一入口
仍是 ``pytest --update-golden``。

算法三句话（票 32 §1）：

1. 对每个 ``(n, seed)`` **只跑一次**引擎，把全部事后判据（``goals.M2_GOALS``）套一遍，
   收集每条基准的候选命中；
2. 每条基准只留**记录条数最少**的候选——更短的局 = 更小的 golden + 更快的门禁回放；
3. **贪心分配**：候选最少的基准先挑，拿"未被别的基准占用"的最小候选，使 27 个
   ``(n, seed)`` 互不相同。

"人数档"由 :data:`TIERS` 表达：人数骨架（``07``–``11``）各自锁死 4/5/6/7/8 人
（票 32 §1 的分布判据），其余 22 条在 4/5/6 人里自由选；"判据表"默认是
``goals.M2_GOALS``。搜索用的驱动就是录制路径本身（``scenarios.play_random``），
否则"搜到的种子"与"录制时跑的局"会各跑各的。搜到的值写死回 ``scenarios.py``，
录制期零搜索。

"与 ``scenarios.py`` 一致"的可执行定义是 :func:`verify_recorded`：重跑写死的
``(人数, 种子)``、确认每条仍命中判据。注意写死的值**早于** ``goals.py`` 与引擎的最后
一次定型（票 32 搜索在前、判据与记录条数在后），故从零搜索的最小候选会漂移，但旧候选
仍然有效；默认把漂移打印出来，``--strict`` 才据此判失败。

用法（在仓库根目录；``tests/`` 是导入根）::

    PYTHONPATH=tests .venv/bin/python -m replays.seed_search
    PYTHONPATH=tests .venv/bin/python -m replays.seed_search --verify   # 只核对写死值
    PYTHONPATH=tests .venv/bin/python -m replays.seed_search --seeds 20  # 冒烟
    PYTHONPATH=tests .venv/bin/python -m replays.seed_search --strict    # 漂移即失败
    PYTHONPATH=tests .venv/bin/python -m replays.seed_search --dump candidates.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from replays import goals, scenarios
from sgs.data import load_content_pack
from sgs.engine import Engine

__all__ = [
    "FREE_TIERS",
    "SKELETON_TIERS",
    "RARE_GOAL_TIERS",
    "TIERS",
    "Candidate",
    "Slot",
    "Tier",
    "allowed_pairs",
    "candidate_grid",
    "choose",
    "collect",
    "compare_with_scenarios",
    "main",
    "reseed_slug",
    "search",
    "tiers_for",
    "verify_recorded",
]

Slot = tuple[int, int]
"""一个搜索坐标 ``(人数, 种子)``：候选、占用集合与网格都用它，别用裸元组。"""


class Candidate(NamedTuple):
    """一条候选命中 ``(记录条数, 人数, 种子)``；按此元组序取"最小候选"。"""

    record_count: int
    player_count: int
    seed: int


@dataclass(frozen=True)
class Tier:
    """一条基准允许的"人数档 × 种子范围"：只扫 ``0 <= seed < seed_count``。"""

    player_count: int
    seed_count: int


FREE_TIERS: tuple[Tier, ...] = (Tier(4, 300), Tier(5, 300), Tier(6, 300))
"""自由档：非人数骨架的 22 条基准可用的 ``(人数, 种子范围)`` 网格。"""

SKELETON_TIERS: dict[str, tuple[Tier, ...]] = {
    "07-identity-4p-rebel-win": (Tier(4, 400),),
    "08-identity-5p-lord-win": (Tier(5, 400),),
    "09-identity-6p-renegade-win": (Tier(6, 700),),
    "10-identity-7p-lord-win": (Tier(7, 200),),
    "11-identity-8p-rebel-win": (Tier(8, 200),),
}
"""人数骨架：4/5/6/7/8 人各锁死一条，种子范围要够大到能搜出命中局。"""


RARE_GOAL_TIERS: dict[str, tuple[Tier, ...]] = {
    "30-halberd-three-targets": (Tier(6, 1000),),
}
"""判据命中率极低、需要更宽种子空间的个别基准（**逐条显式声明**，不是全局放宽）。

``tiers_for`` 先查这张表：声明空间必须**包含** ``scenarios.py`` 写死的值，否则
``test_every_recorded_seed_lies_inside_its_declared_tier`` 判红。今天只有一条：
【方天画戟】"一次指定 3 个目标"要求这张【杀】是**最后的手牌**且场上有 3 个合法目标 ⇒
0–299 里只有 1 个命中（230）。table-ux-overhaul 票 04 重录时 230 的新局涨到 4188 条 /
4.3 MiB（越过 4 MiB 单文件绊线），在 0–1799 里搜到 904（2845 条，约 2.9 MB）⇒ 把这一条的
6 人档种子范围声明为 1000，判据与人数档都不动。
"""


def tiers_for(slug: str) -> tuple[Tier, ...]:
    """一条基准的人数档：个别极稀有判据 → 骨架 → 自由档（顺序就是优先级）。"""
    if slug in RARE_GOAL_TIERS:
        return RARE_GOAL_TIERS[slug]
    return SKELETON_TIERS.get(slug, FREE_TIERS)


TIERS: dict[str, tuple[Tier, ...]] = {
    slug: tiers_for(slug) for slug in scenarios.M2_SCENARIOS
}
"""27 条 M2 基准的完整搜索空间（默认值；``--seeds`` 只用于冒烟缩窄）。"""


def allowed_pairs(tiers: Mapping[str, Sequence[Tier]]) -> dict[str, set[Slot]]:
    """每条基准展开后的坐标集合；贪心分配与扫描共用它。"""
    return {
        slug: {
            (tier.player_count, seed)
            for tier in slug_tiers
            for seed in range(tier.seed_count)
        }
        for slug, slug_tiers in tiers.items()
    }


def candidate_grid(tiers: Mapping[str, Sequence[Tier]]) -> list[Slot]:
    """要跑的坐标全量网格：并集去重后排序，每个坐标只跑一次。"""
    pairs = {pair for pairs in allowed_pairs(tiers).values() for pair in pairs}
    return sorted(pairs)


def collect(
    tiers: Mapping[str, Sequence[Tier]] = TIERS,
    *,
    goal_table: Mapping[str, Callable[[Engine], bool]] | None = None,
    deck_profile: str = scenarios.M2_DECK_PROFILE,
    progress: Callable[[int, int, int, int], None] | None = None,
) -> dict[str, list[Candidate]]:
    """跑完整张网格，返回每条基准的**全部**候选（未去重、未分配）。

    输入三件：``tiers`` 是人数档 × 种子范围，``goal_table`` 是判据表（默认
    ``goals.M2_GOALS``）。每个坐标只跑一次引擎，命中判据的基准各记一条候选；
    未走到 ``game_end`` 的局直接跳过（判据只读终局态）。
    """
    table = goals.M2_GOALS if goal_table is None else goal_table
    allowed = allowed_pairs(tiers)
    grid = candidate_grid(tiers)
    candidates: dict[str, list[Candidate]] = {slug: [] for slug in tiers}
    if not grid:
        return candidates

    pack = load_content_pack(deck_profile=deck_profile)
    for index, (player_count, seed) in enumerate(grid, start=1):
        engine = scenarios.play_random(seed, player_count, pack=pack)
        if engine.records[-1].kind != "game_end":
            continue
        record_count = len(engine.records)
        for slug, pairs in allowed.items():
            if (player_count, seed) not in pairs:
                continue
            if table[slug](engine):
                candidates[slug].append(Candidate(record_count, player_count, seed))
        if progress is not None:
            progress(index, len(grid), player_count, seed)

    return candidates


def choose(candidates: Mapping[str, Sequence[Candidate]]) -> dict[str, Candidate]:
    """贪心分配：候选最少的基准先挑，拿未被占用且最小的候选（纯函数）。"""
    assigned: dict[str, Candidate] = {}
    used: set[Slot] = set()

    for slug in sorted(candidates, key=lambda name: (len(candidates[name]), name)):
        for candidate in sorted(candidates[slug]):
            slot = (candidate.player_count, candidate.seed)
            if slot in used:
                continue
            assigned[slug] = candidate
            used.add(slot)
            break

    return assigned


def search(
    tiers: Mapping[str, Sequence[Tier]] = TIERS,
    **kwargs,
) -> dict[str, Candidate]:
    """从零搜索并分配：:func:`collect` + :func:`choose`。"""
    return choose(collect(tiers, **kwargs))


def compare_with_scenarios(found: Mapping[str, Candidate]) -> list[str]:
    """把搜到的坐标与 ``scenarios.M2_SCENARIOS`` 的写死值逐条比对。"""
    diffs: list[str] = []
    for slug, scenario in scenarios.M2_SCENARIOS.items():
        candidate = found.get(slug)
        expected = (scenario.player_count, scenario.seed)
        if candidate is None:
            diffs.append(f"{slug}: 没搜到候选（期望 {expected}）")
        elif (candidate.player_count, candidate.seed) != expected:
            diffs.append(
                f"{slug}: 搜到 {(candidate.player_count, candidate.seed)}，"
                f"scenarios.py 写死 {expected}"
            )
    return diffs


def verify_recorded() -> list[str]:
    """重跑 ``scenarios.M2_SCENARIOS`` 写死的坐标，确认每条仍命中判据。

    写死的值可能**早于** ``goals.py`` 与引擎的最后一次定型（票 32 搜索在前、定型在后），
    故"从零搜索的最小值"会漂移，但写死的旧候选必须仍然有效——这条就是"与
    ``scenarios.py`` 一致"的可执行定义。
    """
    failures: list[str] = []
    for slug, scenario in scenarios.M2_SCENARIOS.items():
        slot = (scenario.player_count, scenario.seed)
        engine = scenarios.play_random(scenario.seed, scenario.player_count)
        if engine.records[-1].kind != "game_end":
            failures.append(f"{slug}: {slot} 没走到 game_end")
        elif not goals.goal_for(slug)(engine):
            failures.append(f"{slug}: {slot} 不再命中事后判据")
    return failures


def _narrow(tiers: Mapping[str, Sequence[Tier]], seed_count: int) -> dict[str, tuple[Tier, ...]]:
    """把每条基准的每个档位截到 ``seed_count`` 个种子（冒烟用）。"""
    return {
        slug: tuple(
            Tier(tier.player_count, min(tier.seed_count, seed_count)) for tier in slug_tiers
        )
        for slug, slug_tiers in tiers.items()
    }


def _report_progress(index: int, total: int, player_count: int, seed: int) -> None:
    if index % 50 == 0 or index == total:
        print(f"  ... {index}/{total}（n={player_count}, seed={seed}）", flush=True)


def _dump(candidates: Mapping[str, Sequence[Candidate]], path: Path) -> None:
    payload = {
        slug: [list(candidate) for candidate in entries]
        for slug, entries in candidates.items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"候选表写到 {path}")


def reseed_slug(
    slug: str,
    *,
    limit: int = 900,
    progress: Callable[[int, int], None] | None = None,
) -> list[Candidate]:
    """给**一条**基准重选种子（retro S6）；返回按"记录条数最少"排序的候选。

    为什么单独有这个入口：`*.jsonl` 是**逐字节**基准，任何**询问形状**的改动都会移动
    随机驱动的行动序列，于是某条基准的事后判据可能不再命中。本仓的纪律是**重选种子**、
    而不是放宽判据（见 `scenarios.py` 里 38 / 41 的注释）—— 此前这一步是每次现写一段
    一次性脚本（本轮为 38 / 41 写了三次），故收进工具里。

    只支持 ``driver == "random"`` 的基准（M2 / M3）：M1 六条是脚本驱动、种子手写，
    没有"种子搜索"这回事。
    """
    scenario = scenarios.SCENARIOS[slug]
    if scenario.driver != "random":
        raise ValueError(f"{slug} 是脚本驱动（driver={scenario.driver}），没有种子可搜")
    goal = goals.goal_for(slug)
    found: list[Candidate] = []
    for seed in range(limit):
        if progress is not None:
            progress(seed, limit)
        engine = scenarios.play_random(
            seed,
            scenario.player_count,
            deck_profile=scenario.deck_profile,
            heroes=scenario.heroes,
            hero_pool=scenario.hero_pool,
        )
        if goal(engine):
            found.append(Candidate(len(engine.records), scenario.player_count, seed))
    found.sort()
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=None, help="冒烟：把所有档位截到这么多种子")
    parser.add_argument("--dump", type=Path, default=None, help="把原始候选表写成 JSON")
    parser.add_argument(
        "--slug",
        default=None,
        help="只给这一条基准重选种子（retro S6；只支持 driver=random 的 M2/M3 基准）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=900,
        help="--slug 的种子搜索范围 0..limit-1（缺省 900）",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="只重跑 scenarios.py 的写死值并核对判据，不搜索",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="搜索结果与 scenarios.py 写死值不一致时以非零退出（票 32 Done when 的字面口径）",
    )
    args = parser.parse_args(argv)

    if args.slug is not None:
        try:
            hits = reseed_slug(
                args.slug,
                limit=args.limit,
                progress=lambda index, total: _report_progress(index, total, 0, index),
            )
        except KeyError:
            print(f"--slug 失败：没有场景 {args.slug!r}")
            return 2
        except ValueError as error:
            print(f"--slug 失败：{error}")
            return 2
        if not hits:
            print(f"{args.slug}：0..{args.limit - 1} 里没有一个种子命中判据")
            return 1
        print(f"{args.slug}：{len(hits)} 个命中；按记录条数最少排序，前 5：")
        for candidate in hits[:5]:
            print(f"  seed={candidate.seed}\t记录条数={candidate.record_count}")
        return 0

    if args.verify:
        return _report_verification()

    tiers = TIERS if args.seeds is None else _narrow(TIERS, args.seeds)
    candidates = collect(tiers, progress=_report_progress)

    if args.dump is not None:
        _dump(candidates, args.dump)

    found = choose(candidates)
    for slug in scenarios.M2_SCENARIOS:
        candidate = found.get(slug)
        print(f"{slug}\t{tuple(candidate) if candidate is not None else None}")

    unassigned = [slug for slug in scenarios.M2_SCENARIOS if slug not in found]
    drift = compare_with_scenarios(found)
    if drift:
        print(
            "\n注意：本次搜索的最小值与 scenarios.py 的写死值不同——goals.py 与引擎在票 32 "
            "的搜索之后才定型，最小候选会随之漂移；写死值是否仍有效由 --verify 判决，"
            "要按字面口径把漂移当失败就加 --strict："
        )
        for diff in drift:
            print(f"  - {diff}")

    if unassigned:
        print("\n搜索空间太小，这些基准没搜到候选：" + "、".join(unassigned))
        return 1

    total = len(scenarios.M2_SCENARIOS)
    print(f"\n从零搜出 {len(found)}/{total} 条 (人数, 种子)")
    if args.strict and drift:
        return 1
    return _report_verification()


def _report_verification() -> int:
    failures = verify_recorded()
    if failures:
        print("\nscenarios.py 的写死值已失效：")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(
        f"scenarios.py 的 {len(scenarios.M2_SCENARIOS)} 个 (人数, 种子) 仍全部命中各自判据"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - 开发期入口
    raise SystemExit(main())
