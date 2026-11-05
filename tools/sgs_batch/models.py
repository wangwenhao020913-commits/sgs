"""批量对局的数据形状与结果序列化（票 11；spec §1.3 / §1.4）。

两组形状分居两处：**主 JSON**（``sgs.batch.v1``：``config`` / ``summary`` / ``games[]``）
由 :meth:`BatchResult.to_dict` 产出；**决策侧车**（``sgs.batch.actions.v1``，每局一行）由
:meth:`BatchResult.actions_lines` 产出。决策明细只活在库对象
（:attr:`GameResult.actions`）与侧车里，主 JSON 不含它——128 局 × 数百决策会把结果撑到
几十 MB（spec §1.4 的默认关闭口径）。

``action_digest`` 的输入 = 与记录流同一条 canonical JSONL 口径（``sort_keys`` + 紧凑分隔符
+ ``ensure_ascii=False``），故同一批决策在任何进程 / 任何批次里都得到同一个摘要。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sgs.data import Mode, RuleConfig

BATCH_SCHEMA = "sgs.batch.v1"
ACTIONS_SCHEMA = "sgs.batch.actions.v1"

IDENTITY_SIDES: tuple[str, ...] = ("lord", "rebel", "renegade")
NO_IDENTITY_SIDE = "no_identity"
"""``wins_by_side`` 的键：身份局三侧；无身份局只有这一项（胜负没有阵营概念）。"""


@dataclass(frozen=True)
class GameSpec:
    """一局的**完整**入参（票 11 §1.3 的形状）。

    ``mode`` 是显式真源：批量层从不拿 ``players`` 推模式；``players`` 只是座位数。
    ``hero_pool=()`` = 启用池（与 128 局同口径）、``None`` = 无武将、非空元组 = 显式池；
    ``heroes`` 非空时按显式武将走。``seat_policies=None`` = 全 ``heuristic``。
    """

    seed: int
    mode: Mode
    players: int = 5
    rule_config: RuleConfig | None = None
    deck_profile: str | None = None
    heroes: tuple[str, ...] = ()
    hero_pool: tuple[str, ...] | None = ()
    seat_policies: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SeatResult:
    """``games[].seats[]`` 的一条：座位事实 + 是否在胜方（spec §1.4）。"""

    seat: int
    identity: str | None
    hero: str | None
    alive: bool
    won: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "identity": self.identity,
            "hero": self.hero,
            "alive": self.alive,
            "won": self.won,
        }


@dataclass(frozen=True)
class GameResult:
    """一局的结果（spec §1.4）。

    ``outcome`` 已归一成 JSON 可直写的 dict（``winners`` 是 list）；``error`` 非空表示这局
    **失败**，此时 ``finished=False`` / ``outcome=None`` / ``seats=()``，且消息点名到 seed。
    ``journal`` 只在 ``keep_journal=True`` 时非空；``actions`` 只在 ``capture_decisions=True``
    时非空（它是侧车的源，不进主 JSON）。
    """

    seed: int
    mode: str
    finished: bool
    outcome: dict[str, Any] | None
    rounds: int
    decisions: int
    action_digest: str | None
    seats: tuple[SeatResult, ...]
    elapsed_ms: float
    error: str | None = None
    journal: str | None = None
    actions: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """主 JSON 的 ``games[]`` 一条；**不含** ``actions``（决策只进侧车）。"""
        return {
            "seed": self.seed,
            "mode": self.mode,
            "finished": self.finished,
            "outcome": None if self.outcome is None else dict(self.outcome),
            "rounds": self.rounds,
            "decisions": self.decisions,
            "action_digest": self.action_digest,
            "seats": [seat.to_dict() for seat in self.seats],
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "journal": self.journal,
        }


@dataclass(frozen=True)
class BatchResult:
    """一批的结果：``config`` / ``summary`` / ``games`` + 侧车的源（spec §1.4）。"""

    config: dict[str, Any]
    summary: dict[str, Any]
    games: tuple[GameResult, ...]

    def to_dict(self) -> dict[str, Any]:
        """主 JSON ``sgs.batch.v1`` 的完整形状（``elapsed_ms`` 是唯一随机器变化的值）。"""
        return {
            "schema": BATCH_SCHEMA,
            "config": dict(self.config),
            "summary": dict(self.summary),
            "games": [game.to_dict() for game in self.games],
        }

    def actions_lines(self) -> list[str]:
        """决策侧车：**每局一行** ``sgs.batch.actions.v1``（canonical JSON、无换行符）。"""
        return [
            canonical_json(
                {
                    "schema": ACTIONS_SCHEMA,
                    "seed": game.seed,
                    "mode": game.mode,
                    "decisions": list(game.actions),
                }
            )
            for game in self.games
        ]


def canonical_json(payload: Any) -> str:
    """canonical JSON（与记录流的唯一序列化器同一口径），不含换行。

    选项与 ``sgs.engine.journal.record_json`` 同源（``sort_keys`` + 紧凑分隔符 +
    ``ensure_ascii=False``）——那边的入参是 :class:`JournalRecord`，这里的入参是普通
    dict（决策侧车），故不复用那个函数；口径改变时两处必须一起改。
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def hero_pool_label(hero_pool: tuple[str, ...] | None) -> str | list[str] | None:
    """``hero_pool`` 三态 → 主 JSON 的写法：``"enabled"`` / ``null`` / 显式列表（spec §1.4）。"""
    if hero_pool is None:
        return None
    if not hero_pool:
        return "enabled"
    return list(hero_pool)


def compact_action(action: Any) -> dict[str, Any]:
    """决策里的 ``action`` 投影：``card`` 只留 ``card_id`` + ``virtual_sources``。

    牌面事实（牌名 / 花色 / 点数）由 ``card_id`` 可查，侧车不必重复；其余字段原样保留，
    与 ``model_dump(mode="json")`` 同形（spec §1.4）。
    """
    payload = action.model_dump(mode="json")
    card = payload.get("card")
    if isinstance(card, dict):
        payload["card"] = {
            "card_id": card["card_id"],
            "virtual_sources": card.get("virtual_sources") or [],
        }
    return payload


def action_digest(decisions: Sequence[Mapping[str, Any]]) -> str:
    """``action_digest`` = 决策 **canonical JSONL** 的 SHA-256（spec §1.4）。"""
    text = "".join(f"{canonical_json(decision)}\n" for decision in decisions)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize(
    games: Sequence[GameResult], *, players: int, mode: str
) -> dict[str, Any]:
    """按 spec §1.4 汇总一批结果（只统计已完成且无错的对局）。

    - ``wins_by_seat`` 按座位计"是否在 ``outcome.winners`` 里"（身份局含已阵亡的同侧座位）；
    - ``wins_by_side`` 身份局三侧、无身份局记 :data:`NO_IDENTITY_SIDE`；平局进 ``draws``；
    - ``elapsed_ms`` = 各局耗时**之和**（它是唯一不要求跨 worker 相等的字段）。
    """
    completed = [
        game for game in games if game.finished and game.error is None and game.outcome is not None
    ]
    wins_by_seat = [0] * players
    wins_by_side = {
        side: 0 for side in (IDENTITY_SIDES if mode == Mode.IDENTITY.value else (NO_IDENTITY_SIDE,))
    }
    draws = 0
    for game in completed:
        outcome = game.outcome
        assert outcome is not None
        if outcome["result"] == "draw":
            draws += 1
            continue
        for seat in outcome["winners"]:
            wins_by_seat[seat] += 1
        side = outcome["side"] or NO_IDENTITY_SIDE
        wins_by_side[side] = wins_by_side.get(side, 0) + 1

    def _mean(values: list[int]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    return {
        "games": len(games),
        "finished": len(completed),
        "errors": sum(1 for game in games if game.error is not None),
        "draws": draws,
        "wins_by_seat": wins_by_seat,
        "wins_by_side": wins_by_side,
        "mean_rounds": _mean([game.rounds for game in completed]),
        "mean_decisions": _mean([game.decisions for game in completed]),
        "elapsed_ms": round(sum(game.elapsed_ms for game in games), 3),
    }
