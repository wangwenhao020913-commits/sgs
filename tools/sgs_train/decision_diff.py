"""决策 diff：带权表 vs 手写权重（`ai-view` 票 08b 的 λ_B 判据二）。

在固定种子上跑全启发式对局，对每条询问比较两套 argmax：

- **手写**：``features.unweighted_candidate_score``（逐项不乘权 = 08a 的落地态）；
- **带权**：``heuristic._weighted_candidate_score``（生产读口；桶表下按
  ``features.bucket(query)`` 的 token 走桶键 / 基键 / 锚的回落链，见 08a）。

输出的是**变化率**，不是胜负——选 λ_B 要的是"改了多少"，胜负由 ``promote`` 兜。

用法::

    PYTHONPATH=tools .venv/bin/python -m sgs_train.decision_diff \\
        --seeds 88000:88032 --out .scratch/ai-view/t08b/diff-lb3e3.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sgs.agents import features, weights
from sgs.agents import heuristic as heuristic_scoring
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.engine.rng import ai_stream

__all__ = ["decision_diff", "main"]


def _argmax(scores: Sequence[float]) -> frozenset[int]:
    best = max(scores)
    return frozenset(index for index, value in enumerate(scores) if value == best)


def decision_diff(
    seeds: Sequence[int], *, players: int = 5, deck_profile: str = "standard_108"
) -> dict[str, object]:
    """跑一批对局，返回 ``queries / changed / rate`` 与逐询问明细（只记变化的）。"""
    pack = load_content_pack(deck_profile=deck_profile)
    total = 0
    changed: list[str] = []
    original = heuristic_scoring.HeuristicPlayer.respond

    def capture(player, query):
        nonlocal total
        actions = list(query.legal_actions)
        if len(actions) >= 2:
            frames = [features.frame(query, action) for action in actions]
            hand = [features.unweighted_candidate_score(frame) for frame in frames]
            # 带权侧走**生产读口**（`ai-train` 票 08a）：桶化之后 `weights.weight(基键)` 只会
            # 读回锚 1.0，自己拼 Σ 项×权重 会让容量表下的 diff 变成 0% 的空转判据。
            token = features.bucket(query) if weights.has_bucketed_b() else None
            kind = heuristic_scoring.QueryKind(query.kind)
            scorer = heuristic_scoring._SCORERS[kind]
            weighted = [
                heuristic_scoring._weighted_candidate_score(
                    scorer, query, action, token
                )
                for action in actions
            ]
            total += 1
            if _argmax(hand) != _argmax(weighted):
                changed.append(f"{query.kind}@{query.actor}#{query.seq}")
        return original(player, query)

    heuristic_scoring.HeuristicPlayer.respond = capture
    try:
        for seed in seeds:
            print(f"[diff] seed {seed}", file=sys.stderr, flush=True)
            engine = Engine.new_game(
                seed=seed,
                rule_config=RuleConfig(player_count=players),
                pack=pack,
                hero_pool=(),
            )
            engine.run(
                [
                    heuristic_scoring.HeuristicPlayer(ai_stream(seed, seat))
                    for seat in range(players)
                ]
            )
    finally:
        heuristic_scoring.HeuristicPlayer.respond = original
    return {
        "seeds": list(seeds),
        "queries": total,
        "changed": len(changed),
        "rate": (len(changed) / total) if total else 0.0,
        "examples": changed[:10],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sgs_train.decision_diff")
    parser.add_argument("--seeds", required=True, help="闭区间 a:b 或逗号列表")
    parser.add_argument("--players", type=int, default=5)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if ":" in args.seeds:
        start, end = args.seeds.split(":", 1)
        seeds = list(range(int(start), int(end)))
    else:
        seeds = [int(part) for part in args.seeds.split(",")]

    result = decision_diff(seeds, players=args.players)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(
        f"询问 {result['queries']} · 变化 {result['changed']} · "
        f"变化率 {result['rate']:.4f}",
        file=sys.stderr,
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
