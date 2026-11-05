"""串行批量循环、参与者构建与结果归一（票 11；spec §1.3 / §1.5）。

``run_games`` 只做**一次**开跑前的校验（模式组合 / 座位档位 / 种子 / workers / 武将配置），然后每局
交给 :func:`run_one`。每局的异常在 :func:`run_one` 内被收成本局的 ``error`` 并**点名到
seed**——绝不静默缩批，也不让一局的崩溃毒死整批（spec §1.5）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from sgs.agents import HeuristicPlayer, Participant, RandomPlayer
from sgs.data import (
    ContentPack,
    HeroDealError,
    Mode,
    RuleConfig,
    deal_heroes,
    load_content_pack,
)
from sgs.engine import Engine, RandomSource, ai_stream
from sgs.state import Query

from .errors import BatchSpecError, UnsupportedMode
from .models import (
    BatchResult,
    GameResult,
    GameSpec,
    SeatResult,
    action_digest,
    compact_action,
    hero_pool_label,
    summarize,
)

ParticipantsFactory = Callable[[GameSpec], Sequence[Any]]
"""``GameSpec`` → 每座位一个参与者。``workers > 1`` 时它必须是模块顶层可 pickle 的。"""

SEAT_POLICIES: tuple[str, ...] = ("heuristic", "random")
"""座位档位闭集；新增档位在这一处登记（spec §1.3）。"""

MIN_WORKERS = 1
MAX_WORKERS = 8
"""``workers`` 的闭区间（spec §1.5）：默认 1、**不做 auto**。"""


# ---------------------------------------------------------------------------
# 开跑前校验：模式组合 / 座位档位 / 种子 / workers / 武将配置
# ---------------------------------------------------------------------------


def _coerce_mode(mode: Mode | str, players: int) -> Mode:
    """把 ``Mode`` / 字符串归一成枚举；未知值点名失败。"""
    if isinstance(mode, Mode):
        return mode
    try:
        return Mode(mode)
    except ValueError as error:
        allowed = "、".join(item.value for item in Mode)
        raise UnsupportedMode(
            f"未知 mode：{mode!r}（players={players}；可选：{allowed}）"
        ) from error


def _validate_mode(
    players: int, mode: Mode | str, rule_config: RuleConfig | None
) -> tuple[Mode, RuleConfig]:
    """开跑前的一致性校验（spec §1.3 的 fail-closed 边界）。

    当前引擎的 ``RuleConfig.mode`` 仍是 ``player_count`` 的**派生属性**（2 → 无身份、
    4–8 → 身份），故这里显式拒绝"没有引擎实现"的组合，而不是用人数硬套模式。
    """
    resolved = _coerce_mode(mode, players)
    supported = (resolved is Mode.NO_IDENTITY and players == 2) or (
        resolved is Mode.IDENTITY and 4 <= players <= 8
    )
    if not supported:
        raise UnsupportedMode(
            f"不支持的模式组合：players={players}、mode={resolved.value}"
            "（可跑：2 + no_identity、4..8 + identity）"
        )
    if rule_config is None:
        return resolved, RuleConfig(player_count=players)
    if rule_config.player_count != players:
        raise UnsupportedMode(
            f"rule_config.player_count={rule_config.player_count} 与 players={players} 不一致"
        )
    if rule_config.mode is not resolved:
        raise UnsupportedMode(
            f"rule_config 派生的模式是 {rule_config.mode.value}，与显式 mode={resolved.value} 不一致"
        )
    return resolved, rule_config


def _resolve_seat_policies(
    players: int,
    seat_policies: Sequence[str] | None,
    participants_factory: ParticipantsFactory | None,
) -> tuple[str, ...] | None:
    """校验并归一 ``seat_policies``；``None`` 表示由 factory 自建（两者互斥）。"""
    if participants_factory is not None:
        if seat_policies is not None:
            raise BatchSpecError("seat_policies 与 participants_factory 互斥（二选一）")
        return None
    if seat_policies is None:
        return ("heuristic",) * players
    policies = tuple(seat_policies)
    if len(policies) != players:
        raise BatchSpecError(
            f"seat_policies 有 {len(policies)} 项，长度必须等于 players={players}"
        )
    unknown = [policy for policy in policies if policy not in SEAT_POLICIES]
    if unknown:
        raise BatchSpecError(f"未知的座位档位 {unknown}；可选：{list(SEAT_POLICIES)}")
    return policies


def _check_workers(workers: int) -> None:
    if isinstance(workers, bool) or not isinstance(workers, int):
        raise BatchSpecError(f"workers 必须是整数，得到 {type(workers).__name__}：{workers!r}")
    if not MIN_WORKERS <= workers <= MAX_WORKERS:
        raise BatchSpecError(
            f"workers 必须在 {MIN_WORKERS}..{MAX_WORKERS} 内（默认 1、不做 auto），得到 {workers}"
        )


def _validate_heroes(
    *, players: int, heroes: Sequence[str], hero_pool: tuple[str, ...] | None, seed: int
) -> None:
    """用**引擎的同一套发放校验**核武将配置（坏配置是规格错，CLI 退出码 2）。

    显式武将的座位数 / 未知 id / 同真名重复都在 :func:`~sgs.data.deal_heroes` 里判，
    这里只把它的 :class:`HeroDealError` 转成开跑前失败——否则要等某一局开跑才炸，
    并且会以"单局 error"（退出码 1）而不是"规格错"（2）收尾。
    """
    try:
        deal_heroes(
            seat_count=players,
            stream=RandomSource(seed).substream("hero"),
            heroes=tuple(heroes),
            hero_pool=hero_pool,
        )
    except HeroDealError as error:
        raise BatchSpecError(f"武将配置非法：{error}") from error


def _normalise_seeds(seeds: Iterable[int]) -> list[int]:
    """拒绝重复、升序执行（结果同序）——spec §1.3 的批次纪律。"""
    collected: list[int] = []
    for seed in seeds:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise BatchSpecError(f"种子必须是整数，得到 {type(seed).__name__}：{seed!r}")
        collected.append(seed)
    ordered = sorted(collected)
    duplicates = list(
        dict.fromkeys(
            seed for index, seed in enumerate(ordered) if index and ordered[index - 1] == seed
        )
    )
    if duplicates:
        raise BatchSpecError(f"seeds 拒绝重复：{duplicates}")
    return ordered


# ---------------------------------------------------------------------------
# 参与者与逐决策记录（捕获的是应答，不碰 AI 决策本身）
# ---------------------------------------------------------------------------


class _DecisionRecorder:
    """逐决策记录器：计数总在，明细只在 ``capture_decisions=True`` 时记（spec §1.4）。"""

    def __init__(self, capture: bool) -> None:
        self.capture = capture
        self.count = 0
        self.actions: list[dict[str, Any]] = []

    def wrap(self, participant: Participant) -> Participant:
        return _RecordingParticipant(participant, self)

    def record(self, query: Query, action: Any) -> None:
        index = self.count
        self.count += 1
        if not self.capture:
            return
        self.actions.append(
            {
                "index": index,
                "kind": query.kind,
                "actor": query.actor,
                "legal_count": len(query.legal_actions),
                "action": compact_action(action),
            }
        )


class _RecordingParticipant:
    """透明包一层参与者：应答原样返回，只在旁路记一条决策。"""

    def __init__(self, inner: Participant, recorder: _DecisionRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def respond(self, query: Query) -> Any:
        action = self._inner.respond(query)
        self._recorder.record(query, action)
        return action


def _build_participants(
    spec: GameSpec,
    *,
    seat_policies: tuple[str, ...] | None,
    participants_factory: ParticipantsFactory | None,
    recorder: _DecisionRecorder,
) -> list[Participant]:
    """造每座位的参与者并挂上记录器；factory 的返回值必须恰好 ``players`` 个。"""
    if participants_factory is not None:
        built = list(participants_factory(spec))
        if len(built) != spec.players:
            raise BatchSpecError(
                f"participants_factory 返回了 {len(built)} 个参与者，必须等于 players={spec.players}"
            )
    else:
        assert seat_policies is not None
        # 每座位一条 ``ai:<seat>`` 子流（与 ``cli.py`` / 网页会话同口径，票 12）：
        # 座位之间的取数互不消费，A/B 读数可比。
        built = [
            (HeuristicPlayer if policy == "heuristic" else RandomPlayer)(
                ai_stream(spec.seed, seat)
            )
            for seat, policy in enumerate(seat_policies)
        ]
    return [recorder.wrap(participant) for participant in built]


# ---------------------------------------------------------------------------
# 单局 / 整批：结果归一、错误收口、config 与 summary
# ---------------------------------------------------------------------------


def _outcome_dict(engine: Engine) -> dict[str, Any] | None:
    """``Outcome`` → JSON 可直写的 dict；未终局返回 ``None``。"""
    outcome = engine.pending_outcome
    if outcome is None:
        return None
    return {
        "result": outcome.result,
        "winner": outcome.winner,
        "side": outcome.side,
        "winners": list(outcome.winners),
        "reason": outcome.reason,
    }


def _seats(engine: Engine) -> tuple[SeatResult, ...]:
    """每座位一条：身份 / 武将 / 存活 / 是否在胜方（身份局含阵亡的同侧座位）。"""
    outcome = engine.pending_outcome
    winners = frozenset(outcome.winners) if outcome is not None else frozenset()
    return tuple(
        SeatResult(
            seat=player.seat,
            identity=None if player.identity is None else player.identity.value,
            hero=player.hero,
            alive=player.alive,
            won=player.seat in winners,
        )
        for player in engine.state.players
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _error_result(
    spec: GameSpec,
    mode: Mode,
    error: Exception,
    recorder: _DecisionRecorder,
    started: float,
) -> GameResult:
    """单局失败：点名到 seed，其余字段留空（spec §1.5 的"绝不静默缩批"）。"""
    return GameResult(
        seed=spec.seed,
        mode=mode.value,
        finished=False,
        outcome=None,
        rounds=0,
        decisions=recorder.count,
        action_digest=None,
        seats=(),
        elapsed_ms=_elapsed_ms(started),
        error=f"seed={spec.seed}: {type(error).__name__}: {error}",
    )


def run_one(
    spec: GameSpec,
    *,
    pack: ContentPack | None = None,
    keep_journal: bool = False,
    capture_decisions: bool = False,
    participants_factory: ParticipantsFactory | None = None,
) -> GameResult:
    """跑一局并返回 :class:`GameResult`。

    **规格错误照常抛**（:class:`UnsupportedMode` / :class:`BatchSpecError`：``players`` ×
    ``mode`` 不成立、座位档位不合法、factory 返回的座位数不对）；**对局本身的异常**收进
    ``GameResult.error`` 并点名到 seed，供整批继续跑、CLI 以退出码 1 收尾。

    ``pack`` 是**内部**入参：串行路径复用 ``run_games`` 在父进程加载的那一份（worker 进程
    各自在初始化时加载一次并常驻）；直接调用时省略它即按 ``spec.deck_profile`` 现加载。
    """
    mode, rule_config = _validate_mode(spec.players, spec.mode, spec.rule_config)
    seat_policies = _resolve_seat_policies(
        spec.players, spec.seat_policies, participants_factory
    )
    resolved_pack = (
        pack if pack is not None else load_content_pack(deck_profile=spec.deck_profile)
    )
    _validate_heroes(
        players=spec.players,
        heroes=spec.heroes,
        hero_pool=spec.hero_pool,
        seed=spec.seed,
    )
    recorder = _DecisionRecorder(capture_decisions)
    started = time.perf_counter()
    try:
        engine = Engine.new_game(
            seed=spec.seed,
            rule_config=rule_config,
            pack=resolved_pack,
            heroes=spec.heroes,
            hero_pool=spec.hero_pool,
            journal=keep_journal,
        )
        engine.run(
            _build_participants(
                spec,
                seat_policies=seat_policies,
                participants_factory=participants_factory,
                recorder=recorder,
            )
        )
    except BatchSpecError:
        raise
    except Exception as error:  # noqa: BLE001 - 单局失败要收成本局结果，点名到 seed
        return _error_result(spec, mode, error, recorder, started)

    return GameResult(
        seed=spec.seed,
        mode=mode.value,
        finished=engine.finished,
        outcome=_outcome_dict(engine),
        rounds=engine.state.round,
        decisions=recorder.count,
        action_digest=action_digest(recorder.actions) if capture_decisions else None,
        seats=_seats(engine),
        elapsed_ms=_elapsed_ms(started),
        journal=engine.journal() if keep_journal else None,
        actions=tuple(recorder.actions),
    )


def _batch_config(
    *,
    seeds: Sequence[int],
    players: int,
    mode: Mode,
    pack: ContentPack,
    heroes: Sequence[str],
    hero_pool: tuple[str, ...] | None,
    seat_policies: tuple[str, ...] | None,
    keep_journal: bool,
    capture_decisions: bool,
    workers: int,
) -> dict[str, Any]:
    """主 JSON 的 ``config``（spec §1.4 的十一个键）。"""
    return {
        "seeds": list(seeds),
        "players": players,
        "mode": mode.value,
        "pack_id": pack.pack_id,
        "deck_profile": pack.deck_profile,
        "heroes": list(heroes),
        "hero_pool": hero_pool_label(hero_pool),
        "seat_policies": None if seat_policies is None else list(seat_policies),
        "keep_journal": keep_journal,
        "capture_decisions": capture_decisions,
        "workers": workers,
    }


def run_games(
    *,
    seeds: Iterable[int],
    mode: Mode | str,
    players: int = 5,
    rule_config: RuleConfig | None = None,
    deck_profile: str | None = None,
    heroes: Sequence[str] = (),
    hero_pool: Sequence[str] | None = (),
    seat_policies: Sequence[str] | None = None,
    participants_factory: ParticipantsFactory | None = None,
    keep_journal: bool = False,
    capture_decisions: bool = False,
    workers: int = 1,
    on_progress: Callable[[int, int], None] | None = None,
) -> BatchResult:
    """跑一批对局（spec §1.3 的完整签名）。

    ``mode`` 显式必填、不从 ``players`` 派生；``seeds`` 拒绝重复、升序执行、结果同序；
    ``workers=1`` 与 ``workers>1`` 的**结果**（``games[]`` + ``summary``）除 ``elapsed_ms``
    外逐字段相同（每局都新建 Engine 与参与者，不共享进程内状态）；``config.workers``
    如实记录请求值，故两份 ``config`` 只在这一项上不同。``on_progress(done, total)`` 在
    **每局结束后**回调一次，并行时按完成顺序。
    """
    seed_list = _normalise_seeds(seeds)
    resolved_mode, _ = _validate_mode(players, mode, rule_config)
    policies = _resolve_seat_policies(players, seat_policies, participants_factory)
    _check_workers(workers)

    # 父进程加载一次：``config`` 要写**实际生效**的 pack_id / deck_profile；worker 会在
    # 自己的初始化里再加载一次并常驻（spec §1.5）。
    pack = load_content_pack(deck_profile=deck_profile)
    _validate_heroes(
        players=players,
        heroes=heroes,
        hero_pool=None if hero_pool is None else tuple(hero_pool),
        seed=seed_list[0] if seed_list else 0,
    )
    specs = [
        GameSpec(
            seed=seed,
            mode=resolved_mode,
            players=players,
            rule_config=rule_config,
            deck_profile=deck_profile,
            heroes=tuple(heroes),
            hero_pool=None if hero_pool is None else tuple(hero_pool),
            seat_policies=policies,
        )
        for seed in seed_list
    ]
    if workers == 1:
        games: list[GameResult] = []
        for done, spec in enumerate(specs, start=1):
            games.append(
                run_one(
                    spec,
                    pack=pack,
                    keep_journal=keep_journal,
                    capture_decisions=capture_decisions,
                    participants_factory=participants_factory,
                )
            )
            if on_progress is not None:
                on_progress(done, len(specs))
    else:
        from .parallel import run_parallel

        games = run_parallel(
            specs,
            deck_profile=deck_profile,
            keep_journal=keep_journal,
            capture_decisions=capture_decisions,
            participants_factory=participants_factory,
            workers=workers,
            on_progress=on_progress,
        )

    games.sort(key=lambda game: game.seed)
    return BatchResult(
        config=_batch_config(
            seeds=seed_list,
            players=players,
            mode=resolved_mode,
            pack=pack,
            heroes=heroes,
            hero_pool=None if hero_pool is None else tuple(hero_pool),
            seat_policies=policies,
            keep_journal=keep_journal,
            capture_decisions=capture_decisions,
            workers=workers,
        ),
        summary=summarize(games, players=players, mode=resolved_mode.value),
        games=tuple(games),
    )
