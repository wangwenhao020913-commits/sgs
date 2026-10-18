"""种子搜索工具的**纯函数**判据（票 32）。

搜索本身要跑上千局、不进 pytest；这里只钉住它的确定性部分：贪心分配语义、搜索空间
形状，以及"写死的 28 个 ``(n, seed)`` 都落在声明的档位里"。唯一真的跑引擎的是
``play_random`` 的一条冒烟判据（一局），用来钉住搜索与录制共用同一个驱动。
"""

from __future__ import annotations

import pytest

from replays import goals, scenarios, seed_search
from replays.seed_search import (
    SKELETON_TIERS,
    TIERS,
    Candidate,
    Tier,
    allowed_pairs,
    candidate_grid,
    choose,
    collect,
    compare_with_scenarios,
    search,
)


# ---------------------------------------------------------------------------
# 贪心分配（票 32 §1 第 3 句）
# ---------------------------------------------------------------------------


def test_choose_gives_the_scarcest_baseline_first_pick_and_keeps_seeds_unique():
    candidates = {
        "rare": [Candidate(50, 4, 1)],
        "common": [Candidate(10, 4, 1), Candidate(20, 4, 2)],
    }

    chosen = choose(candidates)

    assert chosen["rare"] == Candidate(50, 4, 1), "候选最少的先挑"
    assert chosen["common"] == Candidate(20, 4, 2), "最小候选被占用后顺延到下一个"
    assert len({(c.player_count, c.seed) for c in chosen.values()}) == len(chosen), (
        "贪心必须让 28 个 (人数, 种子) 互不相同"
    )


def test_choose_prefers_the_smallest_unoccupied_candidate_not_the_first():
    candidates = {
        "two": [Candidate(30, 4, 5), Candidate(10, 4, 7)],
        "one": [Candidate(5, 5, 1)],
    }

    chosen = choose(candidates)

    assert chosen["one"] == Candidate(5, 5, 1), "候选只一条的基准先挑"
    assert chosen["two"] == Candidate(10, 4, 7), "在同一条基准内取记录条数最少的那条"


def test_choose_leaves_a_baseline_unassigned_when_every_candidate_is_taken():
    """搜索空间太小时宁可留空，也不悄悄复用别人的 ``(人数, 种子)``。"""
    candidates = {
        "first": [Candidate(1, 4, 0)],
        "second": [Candidate(2, 4, 0)],
    }

    chosen = choose(candidates)

    assert chosen == {"first": Candidate(1, 4, 0)}


# ---------------------------------------------------------------------------
# 搜索空间：人数档与网格（票 32 §1 的"4/5/6/7/8 各 ≥1"）
# ---------------------------------------------------------------------------


def test_candidate_grid_dedupes_and_sorts_the_union_of_every_tier():
    tiers = {"a": (Tier(4, 2),), "b": (Tier(4, 2), Tier(5, 1))}

    assert candidate_grid(tiers) == [(4, 0), (4, 1), (5, 0)]


def test_collect_and_search_are_empty_for_an_empty_search_space():
    assert collect({}) == {}
    assert search({}) == {}


def test_tiers_cover_exactly_the_m2_baselines_and_pin_the_five_seat_counts():
    assert set(TIERS) == set(scenarios.M2_SCENARIOS)

    pinned = {
        slug: {tier.player_count for tier in TIERS[slug]} for slug in SKELETON_TIERS
    }
    assert {n for counts in pinned.values() for n in counts} == {4, 5, 6, 7, 8}, (
        "人数骨架必须覆盖 4/5/6/7/8 人各一条"
    )
    assert all(len(counts) == 1 for counts in pinned.values()), "骨架基准锁死单一人数"


def test_every_recorded_seed_lies_inside_its_declared_tier():
    """写死的 28 个 ``(n, seed)`` 必须落在 :data:`TIERS` 声明的搜索空间里。"""
    allowed = allowed_pairs(TIERS)
    for slug, scenario in scenarios.M2_SCENARIOS.items():
        assert (scenario.player_count, scenario.seed) in allowed[slug], slug


def test_the_tool_only_covers_m2_baselines_never_the_m1_regression_six():
    assert not set(TIERS) & set(scenarios.M1_SCENARIOS), (
        "M1 六条是脚本驱动、种子手写，不进种子搜索"
    )


# ---------------------------------------------------------------------------
# 与 scenarios.py 的一致性（票 32 的 Done when）
# ---------------------------------------------------------------------------


def test_compare_with_scenarios_accepts_the_recorded_assignment():
    recorded = {
        slug: Candidate(0, scenario.player_count, scenario.seed)
        for slug, scenario in scenarios.M2_SCENARIOS.items()
    }

    assert compare_with_scenarios(recorded) == []


def test_compare_with_scenarios_reports_a_drifted_seed_and_a_missing_baseline():
    recorded = {
        slug: Candidate(0, scenario.player_count, scenario.seed)
        for slug, scenario in scenarios.M2_SCENARIOS.items()
    }
    recorded["07-identity-4p-rebel-win"] = Candidate(0, 5, 0)
    del recorded["08-identity-5p-lord-win"]

    diffs = compare_with_scenarios(recorded)

    assert len(diffs) == 2
    assert any("07-identity-4p-rebel-win" in diff for diff in diffs)
    assert any("08-identity-5p-lord-win" in diff for diff in diffs)


def test_play_random_drives_a_full_game_that_hits_the_recorded_goal():
    """``play_random`` 就是录制路径：同一个种子必须跑出同一条命中局。"""
    slug = "07-identity-4p-rebel-win"
    scenario = scenarios.M2_SCENARIOS[slug]

    engine = scenarios.play_random(scenario.seed, scenario.player_count)

    assert engine.records[-1].kind == "game_end"
    assert goals.M2_GOALS[slug](engine)


def test_the_module_exposes_no_gate_entry_hook():
    """它是``python -m``工具，不是 pytest 插件：不定义 pytest 钩子。"""
    for name in dir(seed_search):
        assert not name.startswith("pytest_"), f"种子搜索不得成为门禁入口：{name}"


# ---------------------------------------------------------------------------
# 单条基准重选种子（retro S6）：`--slug` 是**可选**入口，不改 M2-only 的默认面
# ---------------------------------------------------------------------------


def test_reseed_slug_refuses_scripted_baselines():
    """M1 六条是脚本驱动、种子手写 —— 没有"种子搜索"这回事，必须明确拒绝。"""
    m1_slug = next(iter(scenarios.M1_SCENARIOS))
    with pytest.raises(ValueError, match="脚本驱动"):
        seed_search.reseed_slug(m1_slug, limit=1)


def test_reseed_slug_returns_hits_sorted_by_record_count_within_range():
    """真跑引擎的冒烟（少量种子）：命中项都落在范围内，且**按记录条数最少**排序。"""
    # W4-fix-2：42（转化族）改脚本驱动、不能再搜种子；换 M3 随机档里判据最易命中的一条
    # （8 个种子命中 7 个，已足够覆盖排序语义）。
    slug = "36-hero-rejudge-cycle"
    hits = seed_search.reseed_slug(slug, limit=8)

    assert all(0 <= candidate.seed < 8 for candidate in hits)
    assert all(candidate.player_count == scenarios.SCENARIOS[slug].player_count for candidate in hits)
    assert [candidate.record_count for candidate in hits] == sorted(
        candidate.record_count for candidate in hits
    ), "按记录条数最少排序（基准越小越好）"


def test_main_slug_reports_a_missing_or_scripted_scenario(capsys):
    """`--slug` 的两种失败面都要**点名**并以非零退出（不静默当成功）。"""
    assert seed_search.main(["--slug", "no-such-slug"]) == 2
    assert "no-such-slug" in capsys.readouterr().out

    m1_slug = next(iter(scenarios.M1_SCENARIOS))
    assert seed_search.main(["--slug", m1_slug, "--limit", "1"]) == 2
