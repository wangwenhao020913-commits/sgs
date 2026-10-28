"""评估基础设施（票 13；spec §2.1–§2.9）：判决批派生、三档命令、版本加载、台账。

落点 = **工具包的公开面**：``tools/ai_eval`` 的派生 / 配比 / 统计 / 漏动作计数 /
版本加载 / 三档命令（``run_quick`` / ``run_promote`` / ``run_seal`` / ``main``）与它发布
的 JSON、stdout 与退出码。不碰内部函数，也不跑 128 / 768 / 1024 副的真实批次——
门禁只付 4 副极小批的钱（spec §Testing「门禁成本有界」）。

``quick`` 的那条用例走 ``python -m ai_eval``：它同时钉住 ``PYTHONPATH=tools`` 的必要性
（漏写 = 响亮失败，不是静默打错版本）与 §2.8 / §2.9 的 JSON 字段 / 退出码。
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import itertools
import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from ai_eval import (  # noqa: E402 - 测试在 import 前把 tools/ 放上 sys.path
    AiVersionError,
    DecisionFact,
    by_identity,
    compare_pairs,
    count_misses,
    derive_batch,
    derive_sealed_batch,
    diff_decisions,
    fact_from_query,
    identity_quota,
    is_reserved_seed,
    load_version,
    noise_floor,
    paired_diff_ci,
    run_cases,
    seat_zero_identity,
)

# 独立转写 spec §2.7 的 LCG + 1:1:2:1 配比后得到的**金值**；改派生规则必须重录，
# 不是"跑一遍实现再抄回来"（那样永远绿）。
GOLDEN_COMMIT = "a" * 40
GOLDEN_DEALS = 8
GOLDEN_SEEDS = (
    574949586672070296,
    580498417623970468,
    3112483297551320946,
    5460844476121400355,
    10500129897309247501,
    13150798029228579335,
    13734283150621669538,
    14788835692124869577,
)
GOLDEN_SHA256 = "dc8a29e8cac389f19f59d6a1f5c0f70ac044a1ff8bc6ac0bf926c55bbc2e9cb0"


# ---------------------------------------------------------------------------
# §2.7 判决批派生：对固定 commit 稳定、对改一个字节敏感、1:1:2:1 额度正确
# ---------------------------------------------------------------------------


def test_a_commit_derives_the_same_batch_every_time() -> None:
    first = derive_batch(GOLDEN_COMMIT, GOLDEN_DEALS)
    second = derive_batch(GOLDEN_COMMIT, GOLDEN_DEALS)

    assert first.seeds == second.seeds == GOLDEN_SEEDS
    assert first.sha256 == second.sha256 == GOLDEN_SHA256
    assert first.kind == "commit_derived"
    assert first.deals == GOLDEN_DEALS


def test_the_sha_is_the_sha256_of_the_sorted_seed_lines() -> None:
    batch = derive_batch(GOLDEN_COMMIT, GOLDEN_DEALS)

    expected = hashlib.sha256(
        "".join(f"{seed}\n" for seed in sorted(batch.seeds)).encode("utf-8")
    ).hexdigest()

    assert batch.sha256 == expected


def test_the_batch_is_sorted_unique_and_spaced_by_at_least_97() -> None:
    batch = derive_batch(GOLDEN_COMMIT, 32)

    assert list(batch.seeds) == sorted(batch.seeds)
    assert len(set(batch.seeds)) == 32
    assert all(seed >= 97 for seed in batch.seeds)
    gaps = [later - earlier for earlier, later in itertools.pairwise(batch.seeds)]
    assert min(gaps) >= 97, gaps


def test_one_changed_byte_changes_the_whole_batch() -> None:
    """判决批随提交而换：改动一个字节 ⇒ 种子与摘要都不同（spec §2.4 A）。"""
    same = derive_batch("a" * 40, 16)
    changed = derive_batch("a" * 39 + "b", 16)

    assert same.seeds != changed.seeds
    assert same.sha256 != changed.sha256


def test_the_identity_quota_is_one_to_one_to_two_to_one() -> None:
    """1:1:2:1 的 768 副额度与 spec §10 的例子逐值相同（最大余数法）。"""
    assert identity_quota(768) == {"lord": 154, "loyalist": 154, "rebel": 307, "renegade": 153}
    assert identity_quota(128) == {"lord": 26, "loyalist": 26, "rebel": 51, "renegade": 25}
    for deals in (4, 8, 128, 768, 1024, 2048):
        quota = identity_quota(deals)
        assert sum(quota.values()) == deals
        assert quota["rebel"] == pytest.approx(2 * quota["lord"], abs=1)


def test_the_derived_batch_fills_exactly_the_quota() -> None:
    batch = derive_batch("c" * 40, 32)

    quota = identity_quota(32)
    counted = {identity: 0 for identity in quota}
    for seed in batch.seeds:
        counted[seat_zero_identity(seed)] += 1

    assert counted == quota
    assert batch.allocation == quota


def test_the_offline_seat_zero_identity_matches_the_engine() -> None:
    """配比按座位 0 的身份凑；离线算法必须与引擎发身份逐个相等（否则整批配比是错的）。"""
    pack = load_content_pack()
    for seed in (136, 137, 138, 1400, 5000, 7001):
        engine = Engine.new_game(
            seed=seed,
            rule_config=RuleConfig(player_count=5),
            pack=pack,
        )
        assert seat_zero_identity(seed) == engine.state.players[0].identity.value


def test_the_live_band_is_reserved_and_never_derived() -> None:
    """spec §5.6：派生的判决种子落进实机带（7001–7099）就重派生。"""
    assert is_reserved_seed(7001)
    assert is_reserved_seed(7099)
    assert not is_reserved_seed(7000)
    assert not is_reserved_seed(7100)

    batch = derive_batch("d" * 40, 128)
    assert not any(is_reserved_seed(seed) for seed in batch.seeds)


def test_seed_derivation_rejects_nonsense_sizes() -> None:
    for deals in (0, -1):
        with pytest.raises(Exception) as error:
            derive_batch("e" * 40, deals)
        assert "deals" in str(error.value)


# ---------------------------------------------------------------------------
# §2.3 / §2.5 统计纯函数：配对差、bootstrap CI、噪声底、分身份聚合
# ---------------------------------------------------------------------------


def test_the_paired_bootstrap_ci_is_deterministic_and_brackets_the_mean() -> None:
    differences = [1, 0, 1, -1, 0, 1, 1, 0, 0, -1, 1, 0, 1, 1, 0, -1, 0, 1, 0, 1]

    first = paired_diff_ci(differences, resamples=400)
    second = paired_diff_ci(differences, resamples=400)

    assert first == second
    assert first[0] <= sum(differences) / len(differences) <= first[1]
    assert -1.0 <= first[0] <= first[1] <= 1.0


def test_a_uniform_paired_difference_has_a_degenerate_interval() -> None:
    assert paired_diff_ci([1] * 32, resamples=200) == (1.0, 1.0)
    assert paired_diff_ci([0] * 32, resamples=200) == (0.0, 0.0)


def test_the_noise_floor_shrinks_with_deals() -> None:
    """δ = 该 N 下的噪声底（spec §2.3）：768 副约 3pp，且随 N 单调变小。"""
    assert noise_floor(768) == pytest.approx(0.032, abs=0.001)
    assert noise_floor(128) > noise_floor(768) > noise_floor(2048)


def test_by_identity_groups_matched_games_by_the_focus_seat_identity() -> None:
    pairs = [
        ("lord", True, False),
        ("lord", False, False),
        ("rebel", True, True),
        ("rebel", False, False),
        ("renegade", False, False),
    ]

    breakdown = by_identity(pairs)

    assert breakdown["lord"] == {
        "deals": 2,
        "candidate_wins": 1,
        "baseline_wins": 0,
        "candidate_rate": 0.5,
        "baseline_rate": 0.0,
        "diff": 0.5,
    }
    assert breakdown["rebel"]["deals"] == 2
    assert breakdown["rebel"]["diff"] == 0.0
    assert breakdown["renegade"]["diff"] == 0.0
    assert "renegade" in breakdown


def test_compare_pairs_publishes_the_documented_shape() -> None:
    pairs = [
        ("lord", True, False),
        ("lord", False, False),
        ("rebel", True, True),
        ("rebel", True, False),
    ]

    comparison = compare_pairs(pairs)
    payload = comparison.to_dict()

    assert set(payload) == {
        "win_rate_candidate",
        "win_rate_baseline",
        "diff",
        "ci95",
        "by_identity",
    }
    assert payload["win_rate_candidate"] == 0.75
    assert payload["win_rate_baseline"] == 0.25
    assert payload["diff"] == 0.5
    assert payload["ci95"][0] <= payload["diff"] <= payload["ci95"][1]
    assert payload["by_identity"]["lord"]["diff"] == 0.5


def test_compare_pairs_ignores_nothing_but_reports_the_paired_deals() -> None:
    """配对差只数**两臂都打完**的副数：一臂报错就整副退出分母（不静默缩成别的批）。"""
    comparison = compare_pairs([("lord", True, False), ("lord", None, False), (None, True, None)])

    assert comparison.deals == 1
    assert comparison.diff == 1.0


# ---------------------------------------------------------------------------
# §2.11 漏动作口径：Pass-with-options 与 composite 清单（票 15 的 live 同源）
# ---------------------------------------------------------------------------


def test_pass_with_options_counts_pass_answers_among_queries_with_an_option() -> None:
    """§2.11 的字面读法：分母 = 含非 Pass 选项的询问，分子 = 其中答了 Pass 的。"""
    counts = count_misses(
        [
            # 有别的选项，AI 却选了 Pass ⇒ 漏 1
            DecisionFact(kind="DODGE", answered_pass=True, non_pass_legal=True),
            # 有别的选项，AI 选了别的 ⇒ 只在分母
            DecisionFact(kind="DODGE", answered_pass=False, non_pass_legal=True),
            # 只剩 Pass 可答（无牌响应的窗口）⇒ 不在分母
            DecisionFact(kind="DODGE", answered_pass=True, non_pass_legal=False),
            # 强制弃牌：候选全是非 Pass ⇒ 在分母，但应答是弃牌不是 Pass
            DecisionFact(kind="DISCARD", answered_pass=False, non_pass_legal=True),
        ]
    )

    assert counts.pass_with_options == 1
    assert counts.pass_denominator == 3
    assert counts.pass_rate == round(1 / 3, 4)


def test_unknown_offer_facts_are_excluded_instead_of_guessed() -> None:
    counts = count_misses(
        [
            DecisionFact(
                kind="SKILL_ACTIVATION",
                answered_pass=True,
                non_pass_legal=None,
            )
        ]
    )

    assert counts.pass_with_options == 0
    assert counts.pass_denominator == 0
    assert counts.pass_rate is None


def test_unfilled_composite_offers_are_misses_and_filled_ones_are_not() -> None:
    counts = count_misses(
        [
            DecisionFact(
                kind="PLAY_PHASE",
                answered_pass=False,
                composite_offered=True,
                composite_filled=False,
            ),
            DecisionFact(
                kind="PLAY_PHASE",
                answered_pass=False,
                composite_offered=True,
                composite_filled=True,
            ),
            DecisionFact(
                kind="PLAY_PHASE",
                answered_pass=False,
                composite_offered=False,
                composite_filled=True,
            ),
        ]
    )

    assert counts.unfilled_composite == 1
    assert counts.composite_denominator == 2
    assert counts.composite_rate == 0.5


def test_a_decision_fact_reads_a_real_play_phase_query() -> None:
    query, action = _a_real_play_phase_query()

    fact = fact_from_query(query, action)

    assert fact.kind == "PLAY_PHASE"
    assert fact.answered_pass is False
    assert fact.non_pass_legal is True
    assert fact.composite_offered is False


def test_a_decision_fact_sees_the_offer_lists_on_the_query() -> None:
    """复合清单不进记录流（``exclude``），但**询问对象**上有；live 的夹具据此计数。"""
    query, action = _a_real_play_phase_query()
    offered = query.model_copy(update={"preactivations": {"hero.ganglie": ()}})

    assert fact_from_query(offered, action).composite_offered is True


def _a_real_play_phase_query():
    """真实引擎产出的第一次出牌阶段询问 + 记录玩家实际给出的应答（不手抄候选）。"""
    from support.positions import RecordingPlayer, run_until

    from sgs.agents import RandomPlayer
    from sgs.engine import ai_stream

    pack = load_content_pack(deck_profile="basic_53")
    engine = Engine.new_game(seed=7, rule_config=RuleConfig(), pack=pack)
    recorder = RecordingPlayer()
    participants = [recorder, *(RandomPlayer(ai_stream(7, seat)) for seat in range(1, 2))]
    run_until(
        engine,
        participants,
        lambda current: any(q.kind == "PLAY_PHASE" for q in recorder.seen),
    )
    query = next(q for q in recorder.seen if q.kind == "PLAY_PHASE")
    return query, _end_phase_or_first(query)


def _end_phase_or_first(query):
    for candidate in query.legal_actions:
        if candidate.kind == "EndPhase":
            return candidate
    return query.legal_actions[0]


# ---------------------------------------------------------------------------
# §2.6 版本加载：git show → runs 副本 → importlib；缺 tag 响亮失败、不许回退 HEAD
# ---------------------------------------------------------------------------

STUB_HEURISTIC = '''\
"""测试用的假启发式模块：只验证加载面，不参与任何对局。"""


class HeuristicPlayer:
    def __init__(self, stream=None):
        self.stream = stream

    def respond(self, query):
        raise NotImplementedError
'''


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _make_version_repo(tmp_path: Path, *, source: str = STUB_HEURISTIC, tag: str = "v1") -> Path:
    root = tmp_path / "repo"
    target = root / "src" / "sgs" / "agents" / "heuristic.py"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.name=ai-eval-test",
        "-c",
        "user.email=ai-eval-test@example.com",
        "commit",
        "-qm",
        "version",
    )
    _git(root, "tag", tag)
    return root


def test_the_loader_loads_a_tag_from_a_copy_under_runs(tmp_path: Path) -> None:
    repo = _make_version_repo(tmp_path)
    versions = tmp_path / "runs" / "ai-versions"

    loaded = load_version("v1", repo_root=repo, versions_dir=versions)

    assert loaded.commit == _git(repo, "rev-parse", "v1^{commit}")
    assert loaded.heuristic_sha256 == hashlib.sha256(STUB_HEURISTIC.encode()).hexdigest()
    assert loaded.path.parent == versions
    assert loaded.path.read_bytes() == STUB_HEURISTIC.encode()
    # __file__ 指回副本（不是主树）：inspect 读的是 sys.modules[cls.__module__].__file__
    assert Path(inspect.getfile(loaded.player_class)).resolve() == loaded.path.resolve()
    assert loaded.player_class.__module__.startswith("_sgs_ai_version_")


def test_a_missing_tag_fails_loudly_and_never_falls_back_to_head(tmp_path: Path) -> None:
    repo = _make_version_repo(tmp_path)
    versions = tmp_path / "runs" / "ai-versions"

    with pytest.raises(AiVersionError) as error:
        load_version("no-such-tag", repo_root=repo, versions_dir=versions)

    assert "no-such-tag" in str(error.value)
    assert "不静默回退 HEAD" in str(error.value)
    assert not list(versions.glob("*.py")), "缺 tag 时不该落下任何（HEAD 的）副本"


def test_a_corrupted_copy_is_refused_instead_of_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_eval.versions as versions_module

    repo = _make_version_repo(tmp_path)
    versions = tmp_path / "runs" / "ai-versions"

    def _write_tampered(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# tampered\n")

    monkeypatch.setattr(versions_module, "_write_copy", _write_tampered)

    with pytest.raises(AiVersionError) as error:
        load_version("v1", repo_root=repo, versions_dir=versions)

    assert "字节不一致" in str(error.value)


def test_the_ai_v0_tag_loads_and_only_touches_the_public_engine_surface() -> None:
    """`ai-v0` 是原点基线（spec §2.6）：它必须仍可加载，且只依赖 sgs.effects / sgs.state。"""
    loaded = load_version("ai-v0", repo_root=ROOT)

    assert loaded.commit == _git(ROOT, "rev-list", "-n", "1", "ai-v0")
    assert loaded.path.parent == ROOT / "runs" / "ai-versions"
    tree = ast.parse(loaded.path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert {"sgs.effects", "sgs.state"} <= imported_modules
    offenders = sorted(
        module
        for module in imported_modules
        if module.startswith("sgs.") and module not in {"sgs.effects", "sgs.state"}
    )
    assert not offenders, offenders


def test_the_ledger_records_the_origin_row_and_the_seed_bands() -> None:
    """spec §2.6 / §7.1：台账首行 = ai-v0，且四类种子带都有登记。"""
    loaded = load_version("ai-v0", repo_root=ROOT)
    ledger = (ROOT / "docs" / "agents" / "ai-versions.md").read_text(encoding="utf-8")

    assert "ai-v0" in ledger
    assert loaded.commit in ledger
    assert loaded.heuristic_sha256 in ledger
    for needle in ("7001–7099", "判决批", "封印批", "趋势"):
        assert needle in ledger, needle


# ---------------------------------------------------------------------------
# 决策 diff：可复算、按询问类型归因（spec §2.3 条件④）
# ---------------------------------------------------------------------------


def _decision(
    index: int, query_kind: str, actor: int, action_kind: str = "Pass", **payload
) -> dict:
    action = {"answers_seq": index + 1, "kind": action_kind}
    action.update(payload)
    return {
        "index": index,
        "kind": query_kind,
        "actor": actor,
        "legal_count": 2,
        "action": action,
    }


def test_identical_decision_sequences_have_no_divergence() -> None:
    decisions = [_decision(0, "PLAY_PHASE", 0), _decision(1, "DODGE", 1)]

    assert diff_decisions(decisions, list(decisions)) is None


def test_the_first_divergence_is_attributed_to_its_query_kind() -> None:
    candidate = [
        _decision(0, "PLAY_PHASE", 0),
        _decision(
            1,
            "DODGE",
            1,
            action_kind="UseCard",
            card={"card_id": 3, "virtual_sources": []},
        ),
        _decision(2, "PLAY_PHASE", 2),
    ]
    baseline = [
        _decision(0, "PLAY_PHASE", 0),
        _decision(1, "DODGE", 1),
        _decision(2, "PLAY_PHASE", 2),
    ]

    divergence = diff_decisions(candidate, baseline)

    assert divergence is not None
    assert divergence.index == 1
    assert divergence.kind == "DODGE"
    assert divergence.actor == 1
    assert divergence.candidate_action["kind"] == "UseCard"
    assert divergence.baseline_action["kind"] == "Pass"


def test_a_truncated_shorter_arm_is_still_a_divergence() -> None:
    """前缀相同但一边提前终局：diff 不能因为 zip 截断而报"没变"。"""
    candidate = [_decision(0, "PLAY_PHASE", 0)]
    baseline = [_decision(0, "PLAY_PHASE", 0), _decision(1, "DODGE", 1)]

    divergence = diff_decisions(candidate, baseline)

    assert divergence is not None
    assert divergence.index == 1
    assert divergence.candidate_action is None


# ---------------------------------------------------------------------------
# cases：D 类用例的机械读数（spec §2.2；票 14 落地 tests/test_ai_cases.py）
# ---------------------------------------------------------------------------

CASES_MODULE = '''\
import pytest


def test_a_real_case_passes():
    assert True


@pytest.mark.xfail(strict=True, reason="已知蠢点 票 24")
def test_a_known_dumb_point_is_still_red():
    assert False


def test_a_regressed_case_fails():
    assert False
'''


def _make_cases_root(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "repo"
    target = root / "tests" / "test_ai_cases.py"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    return root


def test_the_cases_runner_counts_passed_known_red_and_failed(tmp_path: Path) -> None:
    root = _make_cases_root(tmp_path, CASES_MODULE)

    cases = run_cases(root)

    assert cases.measured is True
    assert cases.total == 3
    assert cases.passed == 1
    assert len(cases.failed) == 1
    assert cases.known_red == 1
    assert cases.green is False
    assert cases.to_dict()["failed"] and "regressed" in cases.to_dict()["failed"][0]


def test_a_fully_green_case_module_is_green(tmp_path: Path) -> None:
    root = _make_cases_root(tmp_path, "def test_ok():\n    assert True\n")

    cases = run_cases(root)

    assert cases.measured is True
    assert cases.total == 1
    assert cases.passed == 1
    assert cases.failed == ()
    assert cases.known_red == 0
    assert cases.green is True


def test_a_missing_case_module_is_unmeasured_not_green(tmp_path: Path) -> None:
    root = tmp_path / "empty-repo"
    root.mkdir()

    cases = run_cases(root)

    assert cases.measured is False
    assert cases.green is None
    assert cases.to_dict() == {"total": 0, "passed": 0, "failed": []}


# ---------------------------------------------------------------------------
# §2.8 / §2.9 三档命令：JSON 字段齐备、退出码、A/A 恒空、封印只出聚合
# ---------------------------------------------------------------------------

RESULT_KEYS = {
    "schema",
    "tier",
    "candidate",
    "baseline",
    "anchor",
    "engine_ref",
    "deck_profile",
    "players",
    "mode",
    "seed_batch",
    "design",
    "vs_baseline",
    "vs_anchor",
    "decision_diff",
    "cases",
    "miss_rate",
    "verdict",
    "errors",
}
"""§2.8 的顶层字段 + 本实现追加的 ``tier`` / ``errors``（均见 `docs/agents/ai-versions.md`）。"""

VERDICT_KEYS = {
    "cases_green",
    "non_inferior",
    "anchor_non_inferior",
    "diff_attributed",
    "human_decision",
}


def _run_ai_eval(*argv: str, with_pythonpath: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if with_pythonpath:
        env["PYTHONPATH"] = str(ROOT / "tools")
    else:
        env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-m", "ai_eval", *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_quick_runs_the_main_entry_point_on_four_deals(tmp_path: Path) -> None:
    """spec §2.9 的最小验收：4 副走完 ``__main__``，JSON / 退出码符合 §2.8。"""
    out = tmp_path / "quick.json"

    completed = _run_ai_eval(
        "quick", "--baseline", "HEAD", "--deals", "4", "--no-cases", "--out", str(out)
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    head = _git(ROOT, "rev-parse", "HEAD")
    assert payload["schema"] == "sgs.ai_eval.v1"
    assert payload["tier"] == "quick"
    assert payload["candidate"] == {
        "ref": head,
        "heuristic_sha256": hashlib.sha256(
            subprocess.run(
                ["git", "-C", str(ROOT), "show", "HEAD:src/sgs/agents/heuristic.py"],
                capture_output=True,
                check=True,
            ).stdout
        ).hexdigest(),
    }
    assert payload["baseline"]["ref"] == "HEAD"
    assert payload["engine_ref"] == head
    assert payload["deck_profile"] == "standard_108"
    assert payload["players"] == 5
    assert payload["mode"] == "identity"
    assert payload["seed_batch"] == derive_batch(head, 4).to_dict()
    assert sum(payload["seed_batch"]["identity_allocation"].values()) == 4
    assert payload["design"] == {"duplicate_play": True, "per_seat_streams": "ai:<seat>"}
    assert payload["vs_baseline"] is None
    assert payload["vs_anchor"] is None
    # A/A（候选与基线同版本）：决策 diff 恒为空（spec §2.5 的装置无偏判据）
    assert payload["decision_diff"] == {
        "deals_differing": 0,
        "first_divergence_by_kind": {},
        "examples": [],
    }
    assert payload["cases"] == {"total": 0, "passed": 0, "failed": []}
    assert payload["miss_rate"]["unfilled_composite"] is None
    assert payload["errors"] == 0
    assert set(payload) == RESULT_KEYS
    assert set(payload["verdict"]) == VERDICT_KEYS
    assert payload["verdict"]["cases_green"] is None
    assert payload["verdict"]["non_inferior"] is None
    assert payload["verdict"]["anchor_non_inferior"] is None
    assert payload["verdict"]["diff_attributed"] is None
    assert payload["verdict"]["human_decision"] is None


def test_quick_without_pythonpath_fails_loudly() -> None:
    """spec §2.9：``PYTHONPATH=tools`` 漏写 ⇒ ``No module named ai_eval``，不是静默打错版本。"""
    completed = _run_ai_eval("quick", "--baseline", "HEAD", "--deals", "2", with_pythonpath=False)

    assert completed.returncode != 0
    assert "No module named ai_eval" in completed.stderr


def test_promote_publishes_both_paired_comparisons_and_the_five_verdict_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ai_eval import main

    out = tmp_path / "promote.json"

    code = main(
        [
            "promote",
            "--baseline",
            "HEAD",
            "--anchor",
            "HEAD",
            "--deals",
            "2",
            "--no-cases",
            "--predicted-kind",
            "DODGE",
            "--out",
            str(out),
        ]
    )

    assert code == 0, capsys.readouterr().err
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["tier"] == "promote"
    assert set(payload) == RESULT_KEYS
    assert set(payload["vs_baseline"]) == {
        "win_rate_candidate",
        "win_rate_baseline",
        "diff",
        "ci95",
        "by_identity",
    }
    assert set(payload["vs_anchor"]) == {"diff", "ci95"}
    # A/A：配对差恰为 0，CI 退化成一个点（spec §2.5：装置确定、无偏）
    assert payload["vs_baseline"]["diff"] == 0.0
    assert payload["vs_baseline"]["ci95"] == [0.0, 0.0]
    assert payload["vs_anchor"] == {"diff": 0.0, "ci95": [0.0, 0.0]}
    assert payload["decision_diff"]["deals_differing"] == 0
    assert payload["verdict"]["non_inferior"] is True
    assert payload["verdict"]["anchor_non_inferior"] is True
    assert payload["verdict"]["cases_green"] is None
    assert payload["verdict"]["diff_attributed"] is True  # 空 diff ⊆ 预测集
    assert payload["verdict"]["human_decision"] is None
    assert payload["errors"] == 0


def test_seal_prints_only_aggregate_readings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ai_eval import main

    out = tmp_path / "seal.json"
    batch = derive_sealed_batch("sealed-test", 2)

    code = main(
        [
            "seal",
            "--batch",
            "sealed-test",
            "--versions",
            "HEAD",
            "ai-v0",
            "--deals",
            "2",
            "--out",
            str(out),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0, captured.err
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["tier"] == "seal"
    assert set(payload) == RESULT_KEYS | {"versions"}
    assert payload["seed_batch"]["kind"] == "sealed"
    assert payload["seed_batch"]["batch"] == "sealed-test"
    assert payload["seed_batch"]["sha256"] == batch.sha256
    assert payload["design"] == {"duplicate_play": False, "per_seat_streams": "ai:<seat>"}
    assert payload["candidate"] is None and payload["baseline"] is None
    assert payload["vs_baseline"] is None and payload["vs_anchor"] is None
    assert payload["decision_diff"] is None
    assert set(payload["verdict"]) == VERDICT_KEYS
    assert set(payload["verdict"].values()) == {None}
    versions = payload["versions"]
    assert [entry["ref"] for entry in versions] == ["HEAD", "ai-v0"]
    for entry in versions:
        assert set(entry) == {
            "ref",
            "commit",
            "heuristic_sha256",
            "deals",
            "win_rate",
            "ci95",
            "by_identity",
            "miss_rate",
        }
        assert entry["deals"] == 2
        assert 0.0 <= entry["win_rate"] <= 1.0
        assert entry["ci95"][0] <= entry["win_rate"] <= entry["ci95"][1]
    # 只出聚合：stdout / stderr 都不出现逐种子行，JSON 也不带 games / seeds
    text = captured.out + captured.err
    assert "seed=" not in text
    for seed in batch.seeds:
        assert f"seed={seed}" not in text
    serialized = json.dumps(payload)
    assert '"games"' not in serialized
    assert '"seeds"' not in serialized


def test_a_batch_with_game_errors_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§2.9 的第三个退出码：结论不可用（有对局报错 / worker 崩溃）⇒ 1，不是 2。"""
    import ai_eval.cli as cli

    fake = {
        "tier": "quick",
        "seed_batch": {"sha256": "0" * 64, "deals": 1},
        "errors": 1,
        "decision_diff": {"deals_differing": 0, "first_divergence_by_kind": {}},
        "cases": {"passed": 0, "total": 0},
        "candidate": {"ref": "0" * 40},
        "baseline": {"ref": "HEAD"},
        "miss_rate": {"pass_with_options": None},
    }
    monkeypatch.setattr(cli, "run_quick", lambda **kwargs: fake)

    code = cli.main(
        ["quick", "--baseline", "HEAD", "--no-cases", "--out", str(tmp_path / "q.json")]
    )

    assert code == 1


def test_spec_errors_exit_with_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ai_eval import main

    assert main(["quick", "--baseline", "HEAD", "--deals", "0"]) == 2
    assert "deals" in capsys.readouterr().err

    assert main(["quick", "--baseline", "no-such-tag", "--deals", "2"]) == 2
    assert "no-such-tag" in capsys.readouterr().err

    with pytest.raises(SystemExit) as error:
        main(["quick", "--deals", "2"])
    assert error.value.code == 2
