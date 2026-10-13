"""一局 = 一个会话：把同步阻塞的引擎接成"可被 HTTP 驱动的回合制"（ADR-012 / 地图 D1、D16）。

**机制（挂起驱动）**：引擎的 ``pending`` 字段与 ``submit()`` 方法本来就是公开面，
而 ``ask()`` 在挂起时**已经**把询问写进了记录流。所以人类座位的参与者只需要
"记下询问、抛 :class:`Halt` 让 ``engine.run()`` 退出来"；应答由接入层
``submit()`` 送回、再 ``run()`` 续跑。**引擎一行不改**，也不使用线程或协程
（``src/sgs/**`` 被源码纪律扫描禁止并发原语，故"等待"只能是"请求还没来"）。

**驱动粒度（D16）**：一次 :meth:`Session.drive` 最多推进到**下一个暂停点或终局**，
绝不"驱动到人类再次被问"——否则 AI 之间的长结算会把一个 HTTP 请求拖成几十秒。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from sgs.agents import HeuristicPlayer, Participant, RandomPlayer
from sgs.data import ContentPack, RuleConfig, load_content_pack, validate_rule_config
from sgs.engine import Engine, RandomSource, new_seed
from sgs.state import Action, Query

__all__ = [
    "AI_TIERS",
    "DEFAULT_AI",
    "DEFAULT_HUMAN_SEAT",
    "DEFAULT_PLAYER_COUNT",
    "DEFAULT_TIMEOUT_SECONDS",
    "Halt",
    "Session",
    "SessionLogEntry",
    "StaleAnswer",
    "create_session",
]

DEFAULT_PLAYER_COUNT: Final[int] = 8
"""缺省 8 人——M4 的验收就是"人类可完整打完一局 8 人身份局"（`06` §M4）。"""

DEFAULT_HUMAN_SEAT: Final[int] = 0
"""缺省坐 0 号位；可改（地图 D3：固定坐主公位等于每局同一体验）。"""

DEFAULT_TIMEOUT_SECONDS: Final[float | None] = 60.0
"""缺省 60 秒；``None`` = 关掉超时（地图 D11）。"""

AI_TIERS: Final[tuple[str, ...]] = ("heuristic", "random")
"""人类座位之外的参与者档位；缺省启发式（地图 D3）。"""

DEFAULT_AI: Final[str] = "heuristic"


class StaleAnswer(Exception):
    """提交的应答对不上当前暂停点，或当前根本没有暂停点（地图 D15 → HTTP 409）。"""

    def __init__(self, pending: Query | None) -> None:
        self.pending = pending
        if pending is None:
            super().__init__("当前没有待应答的询问")
        else:
            super().__init__(f"应答已过期：当前待应答的询问是 seq={pending.seq}")


class Halt(Exception):
    """让 ``engine.run()`` 干净退出的内部信号；``query`` 是退出的原因（挂起驱动）。"""

    def __init__(self, query: Query) -> None:
        self.query = query
        super().__init__(f"会话在 seq={query.seq} 处挂起等待外部应答")


@dataclass(frozen=True)
class SessionLogEntry:
    """接入层事实（超时、连接、刷新）——**不进记录流**，只随会话与录像的侧车保存（D10）。"""

    at: float
    kind: str
    detail: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        """JSON-ready 形态（``detail`` 只装标量与字符串，故无需递归转换）。"""
        return {"at": self.at, "kind": self.kind, "detail": dict(self.detail)}


class _HaltPlayer:
    """人类座位的**替身**：不回答，只把询问交给会话并让引擎退到暂停点。

    真正的应答走 :meth:`Session.submit_answer`（``engine.submit``）——**应答不经过这里**。
    这个类住在接入层而不是 ``sgs.agents`` 是刻意的：给它一个 ``ManualPlayer`` 之类的堂皇名字，
    会让下一个人误以为"引擎有一个可注入的人类参与者"（地图 D19）。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def respond(self, query: Query) -> Action:
        raise Halt(query)


class Session:
    """一局对局的接入层状态：引擎、参与者、座位、游标与侧车日志。

    ``cursor`` 是"已经交给浏览器的最大 ``seq``"；断线刷新后凭它继续取增量（地图 D15）。
    """

    def __init__(
        self,
        *,
        seed: int | None = None,
        player_count: int = DEFAULT_PLAYER_COUNT,
        ai: str = DEFAULT_AI,
        human_seat: int = DEFAULT_HUMAN_SEAT,
        hero_pool: Sequence[str] | None | Literal["enabled"] = "enabled",
        heroes: Sequence[str] = (),
        timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS,
        pack: ContentPack | None = None,
        session_id: str = "",
    ) -> None:
        if ai not in AI_TIERS:
            raise ValueError(f"AI 档位非法：{ai!r}（可选 {AI_TIERS}）")
        if hero_pool == "enabled" and heroes:
            hero_pool = None  # 显式武将优先；池不参与（引擎在 heroes 非空时直接早退）
        rule_config: RuleConfig = validate_rule_config({"player_count": player_count})
        if not 0 <= human_seat < rule_config.player_count:
            raise ValueError(f"人类座位非法：{human_seat}（本局座位 0..{rule_config.player_count - 1}）")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError(f"思考时限必须为正数或 None：{timeout_seconds!r}")

        self.session_id = session_id
        self.seed = new_seed() if seed is None else seed
        self.rule_config = rule_config
        self.human_seat = human_seat
        self.ai = ai
        self.timeout_seconds = timeout_seconds
        self.cursor = 0
        self.session_log: list[SessionLogEntry] = []
        # 询问发出的接入层时刻；由 HTTP 层在用 pending 回话时打戳（超时判定的输入之一）。
        # ``_pending_seq`` 记住这个时刻属于**哪一次**询问：询问换了就必须重新起算，
        # 否则旧询问的时限会顺延到新询问上（报障 1：提交后新询问被当场代答）。
        self.pending_since: float | None = None
        self._pending_seq: int | None = None
        self.timeouts = 0

        content = pack if pack is not None else load_content_pack()
        self.pack = content
        self.heroes: tuple[str, ...] = tuple(heroes)
        # 三态**必须**分得开（否则侧车元数据会把标准局重放成无武将局）：
        #   "enabled"（缺省）= 从**启用池**随机发放，对应引擎的 ``hero_pool=()``；
        #   ``None``        = **无武将模式**，对应引擎的 ``hero_pool=None``；
        #   序列            = 指定池。
        self.hero_pool: tuple[str, ...] | None = (
            ()
            if hero_pool == "enabled"
            else (None if hero_pool is None else tuple(hero_pool))
        )
        self.engine = Engine.new_game(
            seed=self.seed,
            rule_config=rule_config,
            pack=content,
            heroes=self.heroes,
            hero_pool=self.hero_pool,
        )

        self._ai_stream = RandomSource(self.seed).substream("ai")
        self._halt_player = _HaltPlayer(self)
        players: list[Participant] = [
            self._make_ai() if seat != human_seat else self._halt_player
            for seat in range(rule_config.player_count)
        ]
        self.participants: tuple[Participant, ...] = tuple(players)
        self.drive()

    # -- 只读面 -------------------------------------------------------------

    @property
    def finished(self) -> bool:
        """本局是否已到终局。"""
        return self.engine.finished

    @property
    def pending(self) -> Query | None:
        """当前待应答的询问；``None`` = 终局或尚未开始。"""
        return self.engine.pending

    @property
    def log_seq(self) -> int:
        """侧车日志的单调序号（``0`` = 还没有日志）；客户端凭它取增量。"""
        return len(self.session_log)

    def logs_after(self, after: int) -> tuple[SessionLogEntry, ...]:
        """``log_seq > after`` 的侧车日志。"""
        return tuple(self.session_log[after:]) if after >= 0 else tuple(self.session_log)

    def mark_pending(self, now: float) -> None:
        """给"当前挂起"打上接入层时刻（超时的起点）；``pending`` 为空时清掉。

        幂等按**询问身份**（``seq``）而不是"有没有戳"：同一次询问上重复打戳不刷新起点
        （否则每次轮询都会把倒计时重置，超时永远不会到点），但**换了询问**就从这一刻重新
        起算——一次询问一个时限，前一次的思考时长不欠到后一次头上（报障 1）。
        """
        pending = self.pending
        if pending is None:
            self.pending_since = None
            self._pending_seq = None
        elif self._pending_seq != pending.seq:
            self.pending_since = now
            self._pending_seq = pending.seq

    @property
    def hero_assignments(self) -> tuple[str | None, ...]:
        """逐座位武将 id（``None`` = 未发放）；侧车元数据的真源（地图 D12）。"""
        return tuple(player.hero for player in self.engine.state.players)

    def records(self) -> tuple[object, ...]:
        """本局全部记录（全知；接入层按可见性口径裁剪后才下发）。"""
        return tuple(self.engine.records)

    def records_after(self, after: int) -> tuple[object, ...]:
        """``seq > after`` 的记录；``header``（无 ``seq``）只在 ``after < 0`` 时给出。"""
        out: list[object] = []
        for record in self.engine.records:
            seq = getattr(record, "seq", None)
            if seq is None:
                if after < 0:
                    out.append(record)
                continue
            if seq > after:
                out.append(record)
        return tuple(out)

    def next_cursor(self, after: int) -> int:
        """本次下发之后的新游标 = 记录流里最大的 ``seq``（``header`` 不参与）。"""
        cursor = 0 if after < 0 else after
        for record in self.engine.records:
            seq = getattr(record, "seq", None)
            if isinstance(seq, int) and seq > cursor:
                cursor = seq
        return cursor

    # -- 驱动 ---------------------------------------------------------------

    def drive(self) -> None:
        """推进到**下一个暂停点或终局**并返回（D16）。

        ``engine.run`` 只在人类座位的替身处退出（:class:`Halt`）；退出时 ``engine.pending``
        就是那次询问——``ask()`` 早已把 ``QueryRecord`` 写进记录流，故挂起/续跑不会重发询问。
        """
        while not self.engine.finished and self.engine.pending is None:
            try:
                self.engine.run(self.participants)
            except Halt:
                break

    def submit_answer(self, action: object) -> None:
        """把一次应答送回引擎并继续推进；序号对不上即 :class:`StaleAnswer`（D15）。"""
        pending = self.engine.pending
        answers_seq = getattr(action, "answers_seq", None)
        if pending is None or answers_seq != pending.seq:
            raise StaleAnswer(pending)
        self.engine.submit(action)
        self.drive()

    # -- 侧车日志 -----------------------------------------------------------

    def log(self, at: float, kind: str, **detail: object) -> SessionLogEntry:
        """记一条接入层事实（不碰记录流）。"""
        entry = SessionLogEntry(at=at, kind=kind, detail=detail)
        self.session_log.append(entry)
        return entry

    def expire_if_due(self, now: float, choose: Callable[[Query], Action]) -> bool:
        """到点就代答一次，返回是否真的代答了（``choose`` 由 :mod:`sgs_web.timeout` 提供）。

        时钟**当参数传进来**：本模块不 import ``time``，超时判定因此可被注入测试。
        """
        pending = self.pending
        if pending is None or self.timeout_seconds is None or self.pending_since is None:
            return False
        if now - self.pending_since < self.timeout_seconds:
            return False
        action = choose(pending)
        self.engine.submit(action)
        self.timeouts += 1
        self.log(
            now, "timeout", seq=pending.seq, query_kind=pending.kind, answered=action.kind
        )
        self.pending_since = None
        self._pending_seq = None
        self.drive()
        return True

    # -- 内部 ---------------------------------------------------------------

    def _make_ai(self) -> Participant:
        if self.ai == "heuristic":
            return HeuristicPlayer(self._ai_stream)
        return RandomPlayer(self._ai_stream)


def create_session(**kwargs: object) -> Session:
    """建局并把 ``session_id`` 装上（HTTP 层用；测试可直接构造 :class:`Session`）。"""
    session = Session(**kwargs)  # type: ignore[arg-type]
    if not session.session_id:
        session.session_id = f"{session.seed}-{session.rule_config.player_count}p"
    return session
