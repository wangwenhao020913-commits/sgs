"""一局 = 一个会话：把同步阻塞的引擎接成"可被 HTTP 驱动的回合制"（ADR-012 / 地图 D1、D16）。

**机制（挂起驱动）**：引擎的 ``pending`` 字段与 ``submit()`` 方法本来就是公开面，
而 ``ask()`` 在挂起时**已经**把询问写进了记录流。所以人类座位的参与者只需要
"记下询问、抛 :class:`Halt` 让 ``engine.run()`` 退出来"；应答由接入层
``submit()`` 送回、再 ``run()`` 续跑。**规则面一行不改**（票 14 起引擎只多一个
"L0 边界让出"的**驱动开关** ``Engine.run(stop_after_game_start=)``，缺省关），
也不使用线程或协程
（``src/sgs/**`` 被源码纪律扫描禁止并发原语，故"等待"只能是"请求还没来"）。

**驱动粒度（D16）**：一次 :meth:`Session.drive` 最多推进到**下一个暂停点或终局**，
绝不"驱动到人类再次被问"——否则 AI 之间的长结算会把一个 HTTP 请求拖成几十秒。

**暂停点**（table-ux-overhaul 票 14 起有三处，次序就是规则次序）：

1. **L0 边界**——"游戏开始时"（``GAME_START``）派发完、第一回合的回合环还没起跑
   （引擎的 ``_started`` 由假翻真）。建局响应就止在这里：玩家看到的顺序是"安排座位 /
   初始化体力 / 发初始牌 → 游戏开始时"，AI 的第一个回合留给**下一次取增量**
   （现有的 ``after`` 游标机制，零新增协议）。这个停点只停**一次**——撞到之后
   :attr:`Session._l0_boundary_reached` 置真，同一局后面的驱动不再在这里停。
2. **人类座位的询问**——``_HaltPlayer`` 抛 :class:`Halt` 让 ``engine.run()`` 退出来。
3. **终局**。

**开局底稿**（票 14）：:attr:`Session.setup` 是**发牌前**（``build_initial`` 之后、
``_start`` 之前）的那一帧权威事实，只在 ``after < 0`` 的那份视图里下发，前端据此从
"发牌前"起播，不再从 post-init 快照倒推。它由 :meth:`Session.__init__` 在 ``drive()``
**之前**冻结——一旦驱动跑过 ``GAME_START``，那份局面就再也回不来了。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from sgs.agents import HeuristicPlayer, Participant, RandomPlayer
from sgs.data import (
    ContentPack,
    HeroDealConfig,
    HeroPick,
    RuleConfig,
    load_content_pack,
    validate_rule_config,
)
from sgs.engine import Engine, ai_stream, new_seed
from sgs.state import Action, AskPolicy, Query

__all__ = [
    "AI_TIERS",
    "DEFAULT_AI",
    "DEFAULT_HUMAN_SEAT",
    "DEFAULT_PLAYER_COUNT",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_WEB_ASK_POLICY",
    "Halt",
    "RecordWindow",
    "Session",
    "SessionLogEntry",
    "StaleAnswer",
    "create_session",
    "parse_ask_policy",
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


DEFAULT_WEB_ASK_POLICY: Final[AskPolicy] = AskPolicy(auto_skip=True)
"""网页会话的**询问策略缺省值**（table-ux-overhaul 票 02 / spec §2 Q4(a)）。

引擎缺省是 **OFF**（金录像 / AI 对战 / 旧录像回放逐字节可复现）；玩家在网页里是 **ON**
——同一个开关、两个缺省，由"谁是调用方"决定，而不是由规则配置决定（那会进 header 字节面）。
"""


def parse_ask_policy(raw: object, *, default: AskPolicy = DEFAULT_WEB_ASK_POLICY) -> AskPolicy:
    """把建局 body / 侧车 meta 里的 JSON 形态解析成 :class:`~sgs.state.AskPolicy`。

    **两个缺省是两个调用方的口径**：``raw is None``（字段缺席）⇒ ``default``。建局请求用
    网页缺省 ON；``replay_journal`` 回放**旧录像**时显式传引擎缺省 OFF（本票之前的录像没有
    这个字段，它拍下来的行为就是旧口径）。

    接受两种形态：``bool``（简写）与 ``{"auto_skip": bool}``（侧车 meta 的冻结形态）。
    形状不对一律 ``ValueError``（HTTP 层映射成 400，不静默回落）。
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        return AskPolicy(auto_skip=raw)
    if isinstance(raw, Mapping):
        unknown = sorted(set(raw) - {"auto_skip"})
        if unknown:
            raise ValueError(f"询问策略有不认识的字段：{unknown}（只认 auto_skip）")
        if "auto_skip" not in raw or not isinstance(raw["auto_skip"], bool):
            raise ValueError(f"询问策略缺 auto_skip 或它不是布尔值：{raw!r}")
        return AskPolicy(auto_skip=raw["auto_skip"])
    raise ValueError(f"询问策略必须是布尔值或 {{'auto_skip': bool}}：{raw!r}")


class StaleAnswer(Exception):
    """提交的应答对不上当前暂停点，或当前根本没有暂停点（地图 D15 → HTTP 409）。

    ``human_seat`` 是**查看者**（这个会话的人类座位）：409 下发的那份"当前挂起"要据此判
    "这份载荷该不该带观看来的牌面"（票 15 私密观看 / 评审 finding ①）。带上它，409 那条
    裸 ``Query`` 路径才判得出归属；``None`` = 无从判 ⇒ 下发方按 fail-closed 抹掉。
    """

    def __init__(self, pending: Query | None, *, human_seat: int | None = None) -> None:
        self.pending = pending
        self.human_seat = human_seat
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


@dataclass(frozen=True)
class RecordWindow:
    """一次取增量需要的记录流切片——**一趟扫描**的全部产出（票 01）。

    记录流只增且 ``seq`` 单调，所以"取增量"只需要知道四样东西；此前每一样各自全扫一遍
    （``records`` / ``records_after`` / ``next_cursor`` / ``query_actor_map`` / 四条派生
    通道各扫一次），一次 ``view()`` 对记录流扫了 7 趟。现在一趟扫完，派生通道只读
    ``tail``（见 :func:`sgs_web.projection.skill_events_after` 等）。

    - ``records``：全部记录的快照（一份元组，**不是**深拷贝；元素是引擎的只读记录模型）；
    - ``tail``：``seq > after`` 的那一段；``after < 0`` 时含 ``header``（它没有 ``seq``）；
    - ``cursor``：本次下发之后的新游标 = 记录流里最大的 ``seq``；
    - ``query_actors``：``query.seq → query.actor``（**全量**：一条应答的 ``answers_seq``
      可能指向早于本批游标的询问，故应答者判据不能只看 ``tail``）。
    """

    records: tuple[object, ...]
    tail: tuple[object, ...]
    cursor: int
    query_actors: Mapping[int, int]


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
        my_hero: str | None = None,
        banned_heroes: Sequence[str] = (),
        timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS,
        pack: ContentPack | None = None,
        session_id: str = "",
        ask_policy: AskPolicy = DEFAULT_WEB_ASK_POLICY,
        pause_after_game_start: bool = False,
    ) -> None:
        """``pause_after_game_start``（table-ux-overhaul 票 14）＝**建局就停在 L0 边界**。

        打开时（接入层 ``GameRegistry.create`` 的建局口径）这一次 ``drive()`` 只跑到
        "游戏开始时"派发完，人类第一问留给下一次取增量——建局响应因此止于 ``GAME_START``。
        缺省关闭＝直接驱动到人类第一问（库层 / 构造局面夹具 / 单测的旧口径，一字未变）。
        """
        if ai not in AI_TIERS:
            raise ValueError(f"AI 档位非法：{ai!r}（可选 {AI_TIERS}）")
        if not isinstance(ask_policy, AskPolicy):
            raise ValueError(f"询问策略必须是 AskPolicy：{ask_policy!r}")
        if hero_pool == "enabled" and heroes:
            hero_pool = None  # 显式武将优先；池不参与（引擎在 heroes 非空时直接早退）
        if my_hero is not None and not isinstance(my_hero, str):
            raise ValueError(f"点将的武将 id 必须是字符串：{my_hero!r}")
        if isinstance(banned_heroes, str) or not isinstance(banned_heroes, Sequence):
            raise ValueError(f"禁将集合必须是武将 id 的数组：{banned_heroes!r}")
        for hero_id in banned_heroes:
            if not isinstance(hero_id, str) or not hero_id:
                raise ValueError(f"禁将的武将 id 必须是非空字符串：{hero_id!r}")
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
        self.ask_policy = ask_policy
        """本局的**询问策略**（table-ux-overhaul 票 02）：网页缺省 ON，随会话冻结进侧车
        元数据、由 ``replay_journal`` 还原。它是会话事实，**不进记录流**、不进 ``RuleConfig``。"""
        self.cursor = 0
        self.session_log: list[SessionLogEntry] = []
        # 询问发出的接入层时刻；由 HTTP 层在用 pending 回话时打戳（超时判定的输入之一）。
        # ``_pending_seq`` 记住这个时刻属于**哪一次**询问：询问换了就必须重新起算，
        # 否则旧询问的时限会顺延到新询问上（报障 1：提交后新询问被当场代答）。
        self.pending_since: float | None = None
        self._pending_seq: int | None = None
        self.timeouts = 0
        # 接入层的**最后访问时刻**（票 01 的会话回收判据）：由 :meth:`mark_pending` /
        # :meth:`touch` 打戳，时钟一律由调用方传进来（本模块不 import time）。
        # ``0.0`` = 还没有任何接入层访问（直接构造的会话），回收时按"不知道"跳过。
        self.last_access = 0.0
        # 叙事增量缓存（票 01）：形如 ``narrative.NarrativeCursor``，由 ``serve.GameRegistry.view``
        # 建 / 填。本模块只当它是"一局一份、可丢的派生缓存"——不读、不写、不序列化它。
        self.narrative_cursor: Any = None
        # 五谷派生状态的**扫描缓存**（table-ux-overhaul 票 18 / 服务端 D 方案）：键 =
        # (记录条数, 五谷帧 id)，值是两次全记录扫描的中间事实（亮牌集合 / 获得者表）。
        # 由 ``projection.harvest_state`` 独占读写；``view`` 与 ``pending`` 两处派生共用。
        # 与 ``narrative_cursor`` 同姿态：一局一份、可丢的派生缓存，**不是会话事实**——
        # 不进记录流、不进侧车元数据、不进金录像。
        self.harvest_cache: dict[str, Any] = {}

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
        # 点将（票 19）+ 禁将（M6 票 13）：两者都是**会话配置**（引擎的 ``HeroDealConfig``），
        # 与种子一起冻结、不进记录流。点将是单座位手选；禁将是版本粒度、只作用于随机配将，
        # 手选**无视**禁将（玩家点中哪版就发哪版），但手选先占真名、别的座位不再抽到它。
        # 钉的座位是 ``human_seat`` 而不是字面的 0——开局页的「我的座位」可选，
        # 缺省 0 时才与"座位 0 指定"逐字等价。``my_hero=None`` = 不点将（纯随机，默认路径不变）。
        self.my_hero: str | None = my_hero
        self.banned_heroes: tuple[str, ...] = tuple(dict.fromkeys(banned_heroes))
        picks: tuple[HeroPick, ...] = (
            () if my_hero is None else (HeroPick(seat=human_seat, hero=my_hero),)
        )
        self.hero_config = HeroDealConfig(banned_heroes=self.banned_heroes, picks=picks)
        self.engine = Engine.new_game(
            seed=self.seed,
            rule_config=rule_config,
            pack=content,
            heroes=self.heroes,
            hero_pool=self.hero_pool,
            hero_config=self.hero_config,
            ask_policy=ask_policy,
        )

        self._halt_player = _HaltPlayer(self)
        players: list[Participant] = [
            self._make_ai(seat) if seat != human_seat else self._halt_player
            for seat in range(rule_config.player_count)
        ]
        self.participants: tuple[Participant, ...] = tuple(players)
        # L0 边界只停一次：`pause_after_game_start` 关掉时等于"已经越过"（永不在那里停）。
        self._l0_boundary_reached = not pause_after_game_start
        # **开局底稿必须在 drive() 之前冻结**（票 14）：此刻 `engine.state` 是
        # `build_initial` 的产物、`_start` 一条都没跑过——「游戏开始时」的技能（【天成】
        # 会改体力上限、摸牌）一跑，这份局面就再也回不来了。
        # 延迟导入：projection 反向依赖本模块的 Session（模块级导入会成环）。
        from sgs_web.projection import setup_snapshot

        self.setup: dict[str, Any] = setup_snapshot(self)
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

        顺带打上**最后访问时刻**（票 01）：GET / answer / create 三条 HTTP 入口都经过这里，
        故"放弃的对局"与"还开着的页面"用同一个戳区分，不必在每个路由里各记一次。
        """
        self.touch(now)
        pending = self.pending
        if pending is None:
            self.pending_since = None
            self._pending_seq = None
        elif self._pending_seq != pending.seq:
            self.pending_since = now
            self._pending_seq = pending.seq

    def touch(self, now: float) -> None:
        """记一次接入层访问时刻（会话回收的唯一输入；时钟由调用方给）。"""
        self.last_access = now

    @property
    def hero_assignments(self) -> tuple[str | None, ...]:
        """逐座位武将 id（``None`` = 未发放）；侧车元数据的真源（地图 D12）。"""
        return tuple(player.hero for player in self.engine.state.players)

    def records(self) -> tuple[object, ...]:
        """本局全部记录的一份快照（一次拷贝；热路径改用 :meth:`window`，不从全量取增量）。"""
        return self.engine.records

    def window(self, after: int) -> RecordWindow:
        """把一次取增量要的东西**一趟扫完**（票 01；一趟 ≈ 1 ms / 6600 条）。

        此前 ``view()`` 对记录流扫了 7 趟（``records`` / ``records_after`` /
        ``next_cursor`` / 应答者表 / 四条派生通道），每趟都全量。这里的判据只有
        ``getattr(record, "seq")`` / ``kind`` / ``query.actor``——**不做**任何
        ``model_dump`` / JSON 文本往返（那是 2.4 s 的来源）。
        """
        records = self.engine.records
        tail: list[object] = []
        actors: dict[int, int] = {}
        cursor = 0 if after < 0 else after
        for record in records:
            seq = getattr(record, "seq", None)
            if not isinstance(seq, int):
                if after < 0:  # `header`：没有 seq，只在首屏下发
                    tail.append(record)
                continue
            if seq > cursor:
                cursor = seq
            if seq > after:
                tail.append(record)
            if getattr(record, "kind", None) == "query":
                actor = getattr(getattr(record, "query", None), "actor", None)
                if isinstance(actor, int):
                    actors[seq] = actor
        return RecordWindow(
            records=records,
            tail=tuple(tail),
            cursor=cursor,
            query_actors=actors,
        )

    def records_after(self, after: int) -> tuple[object, ...]:
        """``seq > after`` 的记录；``header``（无 ``seq``）只在 ``after < 0`` 时给出。"""
        return self.window(after).tail

    def next_cursor(self, after: int) -> int:
        """本次下发之后的新游标 = 记录流里最大的 ``seq``（``header`` 不参与）。"""
        return self.window(after).cursor

    # -- 驱动 ---------------------------------------------------------------

    def drive(self) -> None:
        """推进到**下一个暂停点或终局**并返回（D16；暂停点见模块 docstring）。

        ``engine.run`` 只在人类座位的替身处退出（:class:`Halt`）；退出时 ``engine.pending``
        就是那次询问——``ask()`` 早已把 ``QueryRecord`` 写进记录流，故挂起/续跑不会重发询问。

        L0 边界（票 14）在一局里只停**一次**：:attr:`_l0_boundary_reached` 为假时把
        ``stop_after_game_start`` 交给引擎，它一让出就置真并返回；之后的驱动不再停在那里。
        """
        while not self.engine.finished and self.engine.pending is None:
            stop_at_l0_boundary = not self._l0_boundary_reached
            try:
                self.engine.run(
                    self.participants, stop_after_game_start=stop_at_l0_boundary
                )
            except Halt:
                break
            if stop_at_l0_boundary:
                # ``run`` 正常返回只有两种：终局，或引擎在 L0 边界让出（``_started``
                # 刚翻真、回合环一条都还没跑）。两种都算"这次已经停过了"。
                self._l0_boundary_reached = True
                return

    def submit_answer(self, action: object) -> None:
        """把一次应答送回引擎并继续推进；序号对不上即 :class:`StaleAnswer`（D15）。

        异常里一并带出 ``human_seat``：409 的下发方（``serve.py``）只有那份裸 ``Query``，
        没有它判不了"这张观看牌面该不该下发"（票 15 / 评审 finding ①）。
        """
        pending = self.engine.pending
        answers_seq = getattr(action, "answers_seq", None)
        if pending is None or answers_seq != pending.seq:
            raise StaleAnswer(pending, human_seat=self.human_seat)
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

    def _make_ai(self, seat: int) -> Participant:
        stream = ai_stream(self.seed, seat)
        if self.ai == "heuristic":
            return HeuristicPlayer(stream)
        return RandomPlayer(stream)


def create_session(**kwargs: object) -> Session:
    """建局并把 ``session_id`` 装上（HTTP 层用；测试可直接构造 :class:`Session`）。"""
    session = Session(**kwargs)  # type: ignore[arg-type]
    if not session.session_id:
        session.session_id = f"{session.seed}-{session.rule_config.player_count}p"
    return session
