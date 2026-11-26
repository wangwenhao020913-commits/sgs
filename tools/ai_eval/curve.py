"""局面曲线读数（`ai-view` 票 09；spec §1 ④ / §2.2 第 2 层）：`ai_eval curve` 的实现。

一次 `curve` 跑一批**自弈局**，在**每个询问**上读一次
:func:`sgs.agents.position.read`（四类结局概率），并按终局结局算对数损失 / Brier；
同时把局面随时间的变化标成**事件打点**（阵亡 / 体力归零 / 关键装备变更）。

**它只读，不进决策路径**：读数在询问生成之后、应答之前算一遍并丢弃；同一次询问的全部
候选共享同一个数，加不加都不改变 argmax（`GLOSSARY.md`「局势打分」）。判据由
`tests/test_position_score.py` 钉住（两次运行 journal 逐字节相同）。

**批次**：与 `quick` / `promote` 同一条 commit 派生（:func:`ai_eval.seeds.derive_batch`），
故"这一版读数来自哪一批"可复现；`--seeds` 仅供开发 / 单测缩小。

**基线**：恒先验 = 配比先验（主公方 / 反贼 / 内奸 = 名单人数 ÷ 总人数，平局 0）。读数表
同时给模型与基线，票面的"优于恒先验"直接读 `delta_log_loss` / `delta_brier`。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sgs.agents import HeuristicPlayer, position
from sgs.data import IDENTITY_ROSTERS, Mode, load_content_pack
from sgs.engine import ai_stream
from sgs.state import PlayerView
from sgs_batch import GameSpec, run_one

from .errors import AiEvalRunError, AiEvalSpecError
from .seeds import SeedBatch, derive_batch
from .versions import git_commit

__all__ = [
    "CurveGame",
    "CurveReport",
    "PRIOR",
    "events_between",
    "prior_for_players",
    "run_curve",
]

SCHEMA = "sgs.ai_eval.v1"
"""结果 JSON 的 schema（复用 `quick` / `promote` 那一条，`tier=curve` 区分形状）。"""

TIER = "curve"

DEFAULT_DEALS = 128
DEFAULT_PLAYERS = 5
DEFAULT_DECK_PROFILE = "standard_108"

_EPS = 1e-12
"""对数损失的截断（平局类先验为 0 时 `log(0)` 不是数）。"""


def prior_for_players(players: int) -> tuple[float, float, float, float]:
    """恒先验：``(主忠方, 反贼, 内奸, 平局)`` = 名单人数 ÷ 总人数（平局 0）。

    这是票面"永远输出配比先验"的基线；与 5 人局 ``(0.4, 0.4, 0.2, 0.0)`` 逐值一致。
    """
    roster = IDENTITY_ROSTERS.get(players)
    if not roster:
        raise AiEvalSpecError(f"没有 {players} 人身份局的配比名单：无法定恒先验")
    total = len(roster)
    lord = sum(1 for identity in roster if identity.value in ("lord", "loyalist"))
    rebel = sum(1 for identity in roster if identity.value == "rebel")
    renegade = sum(1 for identity in roster if identity.value == "renegade")
    return (lord / total, rebel / total, renegade / total, 0.0)


@dataclass(frozen=True, slots=True)
class CurveGame:
    """一局的曲线与读数（``points`` 按询问 seq 升序）。"""

    seed: int
    label: int
    rounds: int
    points: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "label": self.label,
            "rounds": self.rounds,
            "points": list(self.points),
        }


@dataclass(frozen=True, slots=True)
class CurveReport:
    """一批的读数 + 曲线。"""

    batch: SeedBatch
    players: int
    deck_profile: str
    games: tuple[CurveGame, ...]
    readings: dict[str, Any]
    event_counts: dict[str, int]
    candidate: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "tier": TIER,
            "candidate": {"ref": self.candidate},
            "players": self.players,
            "deck_profile": self.deck_profile,
            "seed_batch": self.batch.to_dict(),
            "deals": len(self.games),
            "readings": dict(self.readings),
            "event_counts": dict(self.event_counts),
            "curves": [game.to_dict() for game in self.games],
            "errors": 0,
            "capability_gaps": {"total": 0, "by_version": {}, "incompatible_deals": {}},
        }


class _ReadingPlayer:
    """应答原样返回，旁路记下这一问（**不**改答案、不写视图）。"""

    def __init__(self, inner: Any, sink: list[Any]) -> None:
        self._inner = inner
        self._sink = sink

    def respond(self, query: Any) -> Any:
        self._sink.append(query)
        return self._inner.respond(query)


def _public_snapshot(view: PlayerView) -> tuple[tuple[Any, ...], ...]:
    """逐座位的**公开**状态：``(seat, alive, hp, equip_ids)``（身份可见性不进事件）。"""
    return tuple(
        (
            int(player.seat),
            bool(player.alive),
            int(player.hp),
            tuple(card.card_id for card in player.equip),
        )
        for player in view.players
    )


def events_between(
    previous: tuple[tuple[Any, ...], ...] | None,
    current: tuple[tuple[Any, ...], ...],
) -> tuple[dict[str, Any], ...]:
    """两次公开快照之间的事件打点（阵亡 / 体力归零 / 关键装备变更）。

    - ``death``：上一帧还活着、这一帧已阵亡；
    - ``hp_zero``：体力从正数掉到 0（未必立刻阵亡，濒死结算期间仍可能被救）；
    - ``equip_change``：装备区牌面集合变化（增 / 减逐张列出）——"关键装备"按**装备区**
      整体判，不另写武器 / 防具白名单。

    事件按 ``(seat, 类型序)`` 升序（类型序 = 阵亡 → 体力归零 → 装备变更）：同一份快照序列
    在任何进程里得到同一条事件列表。
    """
    if previous is None:
        return ()
    events: list[dict[str, Any]] = []
    before = {row[0]: row for row in previous}
    for row in current:
        seat, alive, hp, card_ids = row
        old = before.get(seat)
        if old is None:
            continue
        old_alive, old_hp, old_cards = old[1], old[2], old[3]
        if old_alive and not alive:
            events.append({"kind": "death", "seat": int(seat)})
        if old_hp > 0 and hp == 0:
            events.append({"kind": "hp_zero", "seat": int(seat)})
        if set(old_cards) != set(card_ids):
            added = tuple(sorted(set(card_ids) - set(old_cards)))
            removed = tuple(sorted(set(old_cards) - set(card_ids)))
            events.append(
                {
                    "kind": "equip_change",
                    "seat": int(seat),
                    "added": list(added),
                    "removed": list(removed),
                }
            )
    order = {"death": 0, "hp_zero": 1, "equip_change": 2}
    events.sort(key=lambda event: (event["seat"], order[event["kind"]]))
    return tuple(events)


def _label(outcome: dict[str, Any] | None) -> int:
    """终局 ``side`` → 四类下标（``draw`` = 3）。"""
    side = None if outcome is None else outcome.get("side")
    if side == "lord":
        return position.LORD
    if side == "rebel":
        return position.REBEL
    if side == "renegade":
        return position.RENEGADE
    return position.DRAW


def _run_one_game(seed: int, *, players: int, pack: Any) -> CurveGame | None:
    """跑一局并返回曲线；未完成 / 崩了的局返回 ``None``。"""
    readers: list[_ReadingPlayer] = []
    sinks: list[list[Any]] = []

    def factory(spec: Any) -> list[Any]:
        readers.clear()
        sinks.clear()
        for seat in range(spec.players):
            sink: list[Any] = []
            sinks.append(sink)
            readers.append(_ReadingPlayer(HeuristicPlayer(ai_stream(spec.seed, seat)), sink))
        return list(readers)

    result = run_one(
        GameSpec(seed=seed, mode=Mode.IDENTITY, players=players),
        pack=pack,
        participants_factory=factory,
    )
    if not result.finished:
        return None
    label = _label(result.outcome)
    queries = [query for sink in sinks for query in sink]
    queries.sort(key=lambda query: int(query.seq))
    points: list[dict[str, Any]] = []
    previous: tuple[tuple[Any, ...], ...] | None = None
    for query in queries:
        view = query.view
        snapshot = _public_snapshot(view)
        events = events_between(previous, snapshot)
        probabilities = position.read(view).probabilities
        points.append(
            {
                "seq": int(query.seq),
                "seat": int(query.actor),
                "p": [float(value) for value in probabilities],
                "events": list(events),
            }
        )
        previous = snapshot
    return CurveGame(
        seed=int(seed),
        label=label,
        rounds=int(result.rounds),
        points=tuple(points),
    )


def run_curve(
    batch: SeedBatch,
    *,
    players: int = DEFAULT_PLAYERS,
    deck_profile: str = DEFAULT_DECK_PROFILE,
    candidate: str | None = None,
) -> CurveReport:
    """跑一批并返回读数 + 曲线（串行；每局一次引擎重放）。"""
    pack = load_content_pack(deck_profile=deck_profile)
    games: list[CurveGame] = []
    labels: list[int] = []
    model_vectors: list[tuple[float, float, float, float]] = []
    event_counts: dict[str, int] = {}
    for seed in batch.seeds:
        game = _run_one_game(int(seed), players=players, pack=pack)
        if game is None:
            continue
        games.append(game)
        for point in game.points:
            model_vectors.append(tuple(point["p"]))
            labels.append(game.label)
            for event in point["events"]:
                event_counts[event["kind"]] = event_counts.get(event["kind"], 0) + 1
    if not model_vectors:
        raise AiEvalRunError("这一批没有完成任何一局：无法出读数")
    prior = prior_for_players(players)
    log_loss = 0.0
    prior_loss = 0.0
    brier = 0.0
    prior_brier = 0.0
    for label, vector in zip(labels, model_vectors, strict=True):
        log_loss -= math.log(max(vector[label], _EPS))
        prior_loss -= math.log(max(prior[label], _EPS))
        one_hot = [0.0, 0.0, 0.0, 0.0]
        one_hot[label] = 1.0
        brier += sum((vector[index] - one_hot[index]) ** 2 for index in range(4))
        prior_brier += sum((prior[index] - one_hot[index]) ** 2 for index in range(4))
    samples = len(labels)
    readings = {
        "samples": samples,
        "games": len(games),
        "log_loss": log_loss / samples,
        "brier": brier / samples,
        "baseline_log_loss": prior_loss / samples,
        "baseline_brier": prior_brier / samples,
        "delta_log_loss": (prior_loss - log_loss) / samples,
        "delta_brier": (prior_brier - brier) / samples,
    }
    return CurveReport(
        batch=batch,
        players=players,
        deck_profile=deck_profile,
        games=tuple(games),
        readings=readings,
        event_counts=event_counts,
        candidate=candidate or git_commit("HEAD"),
    )


def batch_from_seeds(seeds: Sequence[int]) -> SeedBatch:
    """开发 / 单测用：把显式种子包成一个 ``SeedBatch``（``kind=explicit``）。"""
    values = tuple(int(seed) for seed in seeds)
    if not values:
        raise AiEvalSpecError("显式种子不能为空")
    digest = hashlib.sha256(",".join(str(seed) for seed in values).encode()).hexdigest()
    return SeedBatch(
        kind="explicit",
        deals=len(values),
        seeds=values,
        allocation={},
        sha256=digest,
    )


def default_batch(deals: int = DEFAULT_DEALS) -> SeedBatch:
    """缺省批 = HEAD 的 commit 派生批（与 `quick` / `promote` 同一条派生）。"""
    return derive_batch(git_commit("HEAD"), deals)
