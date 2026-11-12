"""SGS 接入层的 HTTP 门面（ADR-012 / 地图 D2、D7、D15、D16）。

标准库 ``http.server``：**不引任何依赖**。传输取**轮询**而不是 WebSocket——
记录流本来就带单调 ``seq``，"按序号取增量"天然成立，而单机单人类场景下延迟可忽略。

路由（``/api/`` 前缀之外一律当静态资源，从包内 ``static/`` 读）：

===============  ==================================  ==================================
方法              路径                                 作用
===============  ==================================  ==================================
``POST``         ``/api/sessions``                   建局；返回首个挂起与快照
``GET``          ``/api/sessions/<id>?after=&logs=`` 取增量；**顺带判定超时**
``POST``         ``/api/sessions/<id>/answer``       提交应答；推进到下一个挂起或终局
``POST``         ``/api/sessions/<id>/preview``      预发动清单一步的**只读**预览（票 11）
``GET``          ``/api/heroes``                     开局页将池（启用并集、按真名分组的版本）
``GET``          ``/api/hero-presets``               将池存档（5 个常驻槽位 + 活动槽位）
``POST``         ``/api/hero-presets``               保存某个槽位并置为活动（覆盖该槽位）
``GET``          ``/api/decks``                      开局页牌堆档位列表（内容包 decks 的键 + 文案）
``GET``          ``/api/runs``                       录像列表
``GET``          ``/api/runs/<name>``                某局录像（meta + 记录流）
===============  ==================================  ==================================

**一次请求最多推进到下一个暂停点或终局**（D16，由 :meth:`Session.drive` 保证）。
**过期应答 409、结构非法 400、未知会话 404，三种都不写记录、不改状态**（D15）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):  # pragma: no cover - 直接 `python tools/sgs_web/serve.py` 时
    # 脚本直跑：把 ``tools/`` 放进 ``sys.path``，让 ``sgs_web`` 成为一个可导入的顶层包。
    # （相对导入在"脚本直跑"下不成立——那时连父包都没有。）
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sgs_web import REPO_ROOT, ensure_engine_importable

ensure_engine_importable()

from sgs.data import DataLoadError, enabled_hero_ids, hero_by_id, load_content_pack  # noqa: E402
from sgs.engine import IllegalActionError  # noqa: E402
from sgs_web.narrative import NarrativeCursor  # noqa: E402
from sgs_web.projection import (  # noqa: E402
    build_skills,
    card_events_after,
    concealed_by_later_reveal,
    concealed_guohuo_cards,
    damage_events_after,
    decorate_records_card_tags,
    issued_manifest,
    live_tag_deltas_after,
    life_loss_events_after,
    pending_payload,
    skill_events_after,
    visible_record,
    visible_snapshot,
)
from sgs_web.replay import list_runs, load_run, save_run  # noqa: E402
from sgs_web.session import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    RecordWindow,
    Session,
    StaleAnswer,
    create_session,
    parse_ask_policy,
)
from sgs_web.timeout import fallback_action  # noqa: E402

__all__ = [
    "HERO_POOL_SLOTS",
    "GameRegistry",
    "deck_catalog",
    "hero_catalog",
    "load_hero_presets",
    "make_server",
    "main",
    "save_hero_presets",
]

STATIC_ROOT = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8100
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"

HERO_POOL_SLOTS: Final = 5
"""将池存档的槽位个数（M6 票 13 / spec D6）：常驻 5 个，选中即编辑、保存覆盖当前槽位。"""

HERO_PRESETS_FILENAME: Final = "hero-presets.json"
DEFAULT_HERO_PRESETS_PATH: Final = DEFAULT_RUNS_DIR / HERO_PRESETS_FILENAME
"""将池存档的落点：与 serve 的录像 / pid / log 同处（``runs/``，gitignored，spec D6）。

文件是**服务端本地状态**，不进记录流、不进录像侧车——它只是"上次保存的将池"。
"""

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    # chain-death-voices 票 03：卡牌语音是**用户素材**（`static/voices/*.mp3`）。缺了这一行
    # 浏览器拿到的是 `application/octet-stream`，`<audio>` / `Audio` 仍能放，但类型是错的。
    ".mp3": "audio/mpeg",
}


_SAVED: set[str] = set()
"""已经落过盘的 ``<录像目录>::<会话 id>``；**只写一次**的幂等键（地图 D14）。

键里带上录像目录，是因为同一进程可以有多个服务实例（测试就同时跑两个），
而会话 id 是每个注册表各自从 ``s1`` 起编号的。
"""


def hero_catalog() -> list[dict[str, object]]:
    """开局页将池的只读面（M6 票 13）：**启用版本并集**按**真名**分组。

    与发放段**同源**：版本清单来自 ``enabled_hero_ids``（标准 + 界版的启用并集，票 12
    起默认混合池），跨表取武将走 ``hero_by_id``，故"面板上画出来的"与"引擎发得出的"
    不会各抄一份名单而漂开。分组键是数据里的 ``true_name``（**不从显示名剥前缀**：4 名
    只有界版的武将没有可剥的"界"前缀）——同一真名的标准 / 界两版落在同一行，界面因此能
    自解释"同一真名一局只出一个"。

    组的顺序 = 真名在启用池里首次出现的顺序；组内版本顺序 = 数据文件顺序（标准表在前、
    界版包在后）。``versions`` 里带 ``id`` / 显示名 / 版本标记：禁将是**版本粒度**
    （票 12 D5），界面按 ``id`` 划掉一格、按 ``true_name`` 判断这一行还有没有可用版本。
    """
    groups: dict[str, list[dict[str, str]]] = {}
    for hero_id in enabled_hero_ids():
        hero = hero_by_id(hero_id)
        assert hero is not None  # enabled_hero_ids 只从武将表里挑，找不到就是上游违约
        groups.setdefault(hero.true_name, []).append(
            {"id": hero.id, "name": hero.name, "version": hero.version}
        )
    return [
        {"true_name": true_name, "versions": versions}
        for true_name, versions in groups.items()
    ]


def _empty_presets() -> dict[str, Any]:
    """5 个空槽位 + 活动槽位 0（每次新建，避免调用方共享同一份可变列表）。"""
    return {
        "slot_count": HERO_POOL_SLOTS,
        "active": 0,
        "slots": [{"banned_heroes": []} for _ in range(HERO_POOL_SLOTS)],
    }


def _preview_answers(raw: object) -> list[tuple[str, tuple[object, ...]]]:
    """预览请求体里的 ``answers`` → ``[(步名, 值元组)]``（**只**校验形状，非法 ⇒ 400）。

    "步名与顺序对不对"不是这里判的：那是预览自己的判据（它拿着这次下发的清单，按
    ``Step.name`` 逐步比对）。这里只保证载荷是个数组、每项有字符串步名与数组 ``values``
    —— 把 ``{"values": 3}`` 这类形状错当成"空答案"会让预览悄悄回一个错误的步。
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"预览请求的 answers 必须是数组：{raw!r}")
    out: list[tuple[str, tuple[object, ...]]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"预览答案的每一项都必须是对象：{entry!r}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"预览答案缺步名：{entry!r}")
        values = entry.get("values", [])
        if not isinstance(values, list):
            raise ValueError(f"预览答案的 values 必须是数组：{entry!r}")
        out.append((name, tuple(values)))
    return out


def _pick_slots(raw: object, slot_count: int) -> tuple[int, ...]:
    """选牌预览请求体里的 ``picks`` → 面内下标元组（**只**校验形状，非法 ⇒ 400）。

    两种写法都收（与票 11 预览的座位 ``3`` / ``"seat:3"`` 同一姿态）：座位号式的
    ``"slot:3"`` 文本（`StepPool` 的文本候选同口径）与裸整数。**不**猜"越界的当没选"：
    槽位是这一面的下标，越界 / 重复 / 认不出的写法都按结构非法回落 400（fail-closed，
    静默纠正会让界面把"看不见的面"读成"全都点得动"）。
    """
    if not isinstance(raw, list):
        raise ValueError(f"选牌预览的 picks 必须是数组：{raw!r}")
    slots: list[int] = []
    for entry in raw:
        if isinstance(entry, str) and entry.startswith("slot:"):
            entry = entry[len("slot:") :]
        # 只认整数与 `slot:N` 文本：bool 是 int 的子类、float 会被 int() 悄悄截断，先挡掉。
        if isinstance(entry, bool) or not isinstance(entry, (int, str)):
            raise ValueError(f"选牌预览的 picks 只认面内下标：{entry!r}")
        try:
            index = int(entry)
        except (TypeError, ValueError) as error:
            raise ValueError(f"选牌预览的 picks 只认面内下标：{entry!r}") from error
        if not 0 <= index < slot_count:
            raise ValueError(f"选牌预览的 picks 越界（这一面只有 {slot_count} 槽）：{index}")
        if index in slots:
            raise ValueError(f"选牌预览的 picks 有重复的槽位：{index}")
        slots.append(index)
    return tuple(sorted(slots))


def _known_hero_ids() -> set[str]:
    """可被禁的版本全集 = 启用池（与 :func:`hero_catalog` / 发放段同源）。"""
    return set(enabled_hero_ids())


def load_hero_presets(path: Path = DEFAULT_HERO_PRESETS_PATH) -> dict[str, Any]:
    """读将池存档；返回 ``{"slot_count", "active", "slots"}``（``slots[i].banned_heroes``）。

    文件缺失 / 坏 JSON / 结构不符 ⇒ **5 个空槽**：存档是本地便利状态，读不动不该把
    开局页炸掉（保存会覆盖它）。旧文件里已不在启用池的武将 id 在读取时丢掉——它们
    禁不出任何东西，留着只会让下一次建局 fail-closed 报一个玩家看不懂的错。
    """
    presets = _empty_presets()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return presets
    if not isinstance(raw, dict):
        return presets
    slots = raw.get("slots")
    if not isinstance(slots, list) or len(slots) != HERO_POOL_SLOTS:
        return presets
    known = _known_hero_ids()
    cleaned: list[dict[str, list[str]]] = []
    for slot in slots:
        banned = slot.get("banned_heroes") if isinstance(slot, dict) else None
        if not isinstance(banned, list):
            return presets
        kept: list[str] = []
        for hero_id in banned:
            if isinstance(hero_id, str) and hero_id in known and hero_id not in kept:
                kept.append(hero_id)
        cleaned.append({"banned_heroes": kept})
    active = raw.get("active")
    if isinstance(active, bool) or not isinstance(active, int) or not 0 <= active < HERO_POOL_SLOTS:
        active = 0
    presets["active"] = active
    presets["slots"] = cleaned
    return presets


def save_hero_presets(
    *,
    slot: object,
    banned_heroes: Sequence[object],
    path: Path = DEFAULT_HERO_PRESETS_PATH,
) -> dict[str, Any]:
    """保存某个槽位（**覆盖**该槽位）并置为 active；返回保存后的全量存档。

    这是将池存档的唯一写入口：槽位越界 / 未知武将 id 一律 ``ValueError``（HTTP 层映射
    400）且**不落盘**——半份存档比没存档更难查。写盘先 ``.tmp`` 再 ``os.replace``，
    与录像落盘同一条纪律（进程被杀不留半份）。
    """
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < HERO_POOL_SLOTS:
        raise ValueError(f"将池槽位必须是 0..{HERO_POOL_SLOTS - 1} 的整数：{slot!r}")
    if isinstance(banned_heroes, str) or not isinstance(banned_heroes, Sequence):
        raise ValueError(f"禁将集合必须是武将 id 的数组：{banned_heroes!r}")
    known = _known_hero_ids()
    cleaned: list[str] = []
    for hero_id in banned_heroes:
        if not isinstance(hero_id, str) or hero_id not in known:
            raise ValueError(f"禁将里有未知武将 id：{hero_id!r}")
        if hero_id not in cleaned:
            cleaned.append(hero_id)
    presets = load_hero_presets(path)
    presets["slots"][slot] = {"banned_heroes": cleaned}
    presets["active"] = slot
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"schema_version": 1, **presets}, ensure_ascii=False, sort_keys=True, indent=2
    )
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)
    return presets


DECK_PROFILE_DISPLAY: dict[str, tuple[str, str]] = {
    "standard_108": ("标准 108", "标准版全部 108 张牌（默认档）。"),
    "standard_ex_160": ("军争 160", "标准 108 张 + 军争 52 张，共 160 张。"),
}
"""开局页**提供给人选**的牌堆档位与文案（标签 + 说明）。

这不是第二份档位来源：会话 / CLI 的值域仍是内容包 ``decks`` 的全部键（加载器校验）；
这里只决定**开局页铺哪几项**。``basic_53`` 是 M1 回归网的 53 张测试切片
（``tests/data/test_deck_snapshot.py``），不对人开放，故不在此表。
"""


def deck_catalog() -> list[dict[str, object]]:
    """开局页「牌堆档位」的只读面：可选档位 + 服务端文案 + 默认标记。"""
    pack = load_content_pack()
    catalog: list[dict[str, object]] = []
    for profile_id, (name, description) in DECK_PROFILE_DISPLAY.items():
        deck = pack.decks.get(profile_id)
        if deck is None:  # 数据删了档位而文案表没跟：开局页不出现死选项（测试会报漂移）
            continue
        catalog.append(
            {
                "id": profile_id,
                "name": name,
                "description": description,
                "cards": len(deck.cards),
                "default": profile_id == pack.deck_profile,
            }
        )
    return catalog


KEEP_FINISHED_SESSIONS = 2
"""终局会话在内存里最多保留几局（perf-web 票 01，用户 2026-11-08 指定）。

一局 8 人长局在引擎里是 1.4 万+ 条记录（其中 `query` 带完整 `legal_actions`，20MB 级）。
此前 `sessions` 只增不删、终局落盘后也不回收 ⇒ 每开一局进程内存 +20MB 常驻，GC/swap 把
服务拖钝。**回放本来就走 `runs/` 里的文件**（`/api/runs/<name>`），内存里只需要"还在打的"
以及"刚打完、页面可能还在轮询的那几局"，故留最近 2 局当缓冲。
"""

ABANDONED_SESSION_SECONDS = 30 * 60.0
"""未终局会话**多久没人访问**就算放弃（table-ux-overhaul 票 01）。

旧口径只在 `save_if_finished` 里回收 ⇒ 只有**打到终局**的局才会被回收；中途关掉标签页 /
输了就跑的局永远留在 `GameRegistry.sessions` 里，实测每局 +9~23 MB（RSS 47→64→87→101→110
MB / 4 局）——这就是"同一进程第二局也卡"的机制。阈值取 30 分钟：比任何一次真的思考 / 看
录像都长，又远短于"人已经走了、进程还开着"。
"""

KEEP_LIVE_SESSIONS = 2
"""未终局会话的**保底面**：最近访问的这几局永不按空闲回收（table-ux-overhaul 票 01）。

"最近"按 `Session.last_access` 算。与 :data:`KEEP_FINISHED_SESSIONS` 同姿态：内存里只需
"还在打的"那几局，超出且久未访问的局回收后客户端拿 404（页面自己会提示重新开局）。
"""


class GameRegistry:
    """内存里的会话表；**一局一个会话**，进程重启即丢（D15、`06` §2"单机跑通再谈"）。

    **会话回收**（perf-web 票 01 + table-ux-overhaul 票 01）三条口径：

    1. **终局落盘后**回收较旧的已终局会话（保留最近 :data:`KEEP_FINISHED_SESSIONS` 局）；
    2. **建新局时**回收"久未访问且未终局"的会话（保留最近 :data:`KEEP_LIVE_SESSIONS` 局）——
       中途放弃的局此前永远留在内存里，这是"同一进程第二局也卡"的机制；
    3. **每次取增量**时顺带按同一判据扫一眼（闲置进程不靠"有人建局"才回收）。

    被回收的会话再取 ⇒ :meth:`get` 抛 ``KeyError``（HTTP 层 404）；它的回放改走文件，
    不受影响。
    """

    def __init__(self, runs_dir: Path | None = None) -> None:
        self.sessions: dict[str, Session] = {}
        self.runs_dir = Path(runs_dir) if runs_dir is not None else DEFAULT_RUNS_DIR
        self.presets_path = self.runs_dir / HERO_PRESETS_FILENAME
        self._counter = 0
        self._finished_order: list[str] = []

    # -- 将池存档（M6 票 13） ----------------------------------------------

    def hero_presets(self) -> dict[str, Any]:
        """读 5 个常驻槽位 + 上次保存的 active（开局页自动加载它）。"""
        return load_hero_presets(self.presets_path)

    def save_hero_presets(self, body: dict[str, Any]) -> dict[str, Any]:
        """保存一个槽位并置为 active；**校验只在模块函数**（唯一写入口）里做。"""
        return save_hero_presets(
            slot=body.get("slot"),
            banned_heroes=body.get("banned_heroes", []),
            path=self.presets_path,
        )

    # -- 建局 ---------------------------------------------------------------

    def create(self, body: dict[str, Any]) -> Session:
        deck_profile = body.get("deck_profile") or None
        if deck_profile is not None and not isinstance(deck_profile, str):
            raise ValueError(f"牌堆档位必须是字符串：{deck_profile!r}")
        # 档位的唯一来源 = 内容包 ``decks`` 的键；缺省（None）= 包自带默认档。
        # 未知档位在这里抛 DataLoadError，由 HTTP 层映射成 400——**先加载、后登记**，
        # 故失败的请求不会在 registry 里留下半局会话。
        pack = load_content_pack(deck_profile=deck_profile)
        self._counter += 1
        session = create_session(
            seed=body.get("seed"),
            player_count=int(body.get("player_count", 8)),
            ai=str(body.get("ai", "heuristic")),
            human_seat=int(body.get("human_seat", 0)),
            my_hero=body.get("my_hero") or None,
            # 禁将（M6 票 13）：**版本粒度、只作用于随机配将**；随建局请求冻结进会话配置，
            # 不读本地存档——存档只是开局页的编辑器初值，玩家点了「开始」就以当时的集合为准。
            banned_heroes=body.get("banned_heroes") or (),
            timeout_seconds=body.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            # 询问策略（table-ux-overhaul 票 02）：会话配置，缺省 **ON**（网页口径）。
            # 引擎缺省 OFF 的旧口径由"字段缺席"以外的一切调用方保持，见 AskPolicy。
            ask_policy=parse_ask_policy(body.get("ask_policy")),
            pack=pack,
            session_id=f"s{self._counter}",
            # L0 边界（table-ux-overhaul 票 14）：建局响应止于"游戏开始时"派发完——
            # 这是**接入层的建局口径**，故只在这一条生产路径上打开；库层（单测 / 构造
            # 局面夹具）缺省仍是一路驱动到人类第一问。
            pause_after_game_start=True,
        )
        session.mark_pending(time.monotonic())
        self.sessions[session.session_id] = session
        # **先登记、再回收**（table-ux-overhaul 票 01）：建局这一刻是回收"中途放弃的局"的
        # 时机——它们不会因为不再有人访问而自己消失。新局刚刚打了"此刻"的访问戳，永远排在
        # "最近 N 局"里，不会被这一趟误收。
        self._prune_abandoned(time.monotonic())
        return session

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    # -- 取增量（**超时判定的唯一触发点**） ---------------------------------

    def advance_clock(self, session: Session) -> None:
        """取增量这一条路上的两件事：**把引擎推到下一个暂停点** + 到点就代答。

        前者是 table-ux-overhaul 票 14 的 L0 边界：建局响应止于 ``GAME_START``，人类第一问
        要由**下一次取增量**驱动出来（``Session.drive`` 只在 L0 边界停一次，第二趟就一路
        跑到人类第一问）。对已经停在询问上 / 已终局的会话它是零动作。
        顺序不能倒：先驱动、后打戳（``mark_pending`` 看到的那一问才是这一趟刚产出的那一问，
        倒计时从它出现的那一刻起算）。
        """
        session.drive()
        now = time.monotonic()
        # `mark_pending` 顺带打上"最后访问时刻"（票 01 的会话回收判据）。
        session.mark_pending(now)
        # 取增量是全进程最频繁的入口：顺手扫一次放弃的局，不必等"有人建新局"才回收。
        self._prune_abandoned(now)
        while session.expire_if_due(time.monotonic(), fallback_action):
            pass

    def view(self, session: Session, *, after: int, logs_after: int) -> dict[str, Any]:
        """会话视角的响应体（建局 / 取增量 / 应答三处**共用同一个形状**）。

        **记录流只扫一趟**（table-ux-overhaul 票 01）：`Session.window(after)` 一次给出
        本批切片 / 新游标 / 应答者表 / 序号上界；四条派生通道读**尾部窗口**而不是整条流，
        叙事走会话自己的增量缓存。此前这里对记录流扫了 7 趟，其中四条通道把整条流
        `model_dump + json.dumps + json.loads` 一遍（N=6612 时 2.38 s / 单次 view 2.7 s）。
        """
        batch = session.window(after)
        window = batch.tail
        # U4-06 / D13：`action` 记录按查看者放行——只有人类座位自己的应答展开细节。
        # 应答者 = `answers_seq` 指向的那次询问的 actor；映射取**全量**记录（询问可能
        # 早于 `after` 游标），展开只对窗口里的记录做。
        query_actors = batch.query_actors
        # 掩码按"**这批记录里**谁在验明之前就在处理区"算，而不是只按当下的结算栈算：
        # 一次没有目标步的【蛊惑】会在同一批记录里走完扣置 → 验明，等这批下发时栈上已经
        # 不剩什么，只看栈就会把扣置那条搬牌记录连同**源牌牌面**一起发出去（用户实机
        # 报障 2026-11-02 §2 的残留）。两半同源：栈上还挂着的（画"声明的牌名"）+ 本窗口
        # 里先进处理区、后被亮出的（只掩码）。
        concealed = {
            **concealed_by_later_reveal(window, session.engine.state),
            **concealed_guohuo_cards(session.engine),
        }
        records = [
            payload
            for record in window
            if (
                payload := visible_record(
                    record,
                    viewer=session.human_seat,
                    query_actors=query_actors,
                    state=session.engine.state,
                    # UI1/UI2：未验明的【蛊惑】扣置牌**不给牌面**（处理区是公开区，
                    # 但扣置是"背面朝上"进去的）——事件流与舞台同一份判据。
                    concealed=concealed,
                )
            )
            is not None
        ]
        snapshot = visible_snapshot(session)
        # 标签 delta 仍不进人类记录流（`visible_record` 裁掉）；这张牌的**可见**增删
        # 另走展示通道，前端据此逐事件更新 live view，不必等下一次询问。
        decorate_records_card_tags(records, session.engine.state, session.human_seat)
        payload: dict[str, Any] = {
            "session_id": session.session_id,
            "seed": session.seed,
            "player_count": session.rule_config.player_count,
            "human_seat": session.human_seat,
            "heroes": list(session.hero_assignments),
            "deck_profile": session.pack.deck_profile,
            "timeout_seconds": session.timeout_seconds,
            "pending_since": session.pending_since,
            "finished": session.finished,
            "cursor": batch.cursor,
            "log_seq": session.log_seq,
            "snapshot": snapshot,
            "skills": build_skills(session, session.human_seat),
            "pending": pending_payload(session),
            "records": records,
            "live_deltas": live_tag_deltas_after(
                window,
                after=after,
                state=session.engine.state,
                snapshot=snapshot,
                viewer=session.human_seat,
            ),
            # U1-05 叙事契约：简易事件流的条目由服务端生成、按查看者过滤；前端只渲染。
            # 游标复用记录流的 `after`——条目自带产生它的记录 `seq`，两批拼接 = 全量。
            "narrative": self._narrative(session, batch, after),
            # U3-04 / D8.2 + lei 票 06：技能发动是**派生通道**，不是记录流的新事实——
            # 两路合成同一条 `skill_events`：① 未裁剪记录里「已接受、动作点名了技能」的
            # 那几条（补上被 `visible_record` 裁掉的 skill_id / choices）；② 引擎的发动缓冲
            # `Engine.skill_activations`（记录面没有技能归属的锁定技 / 转化技）。与
            # `records` 共用同一个 `after` 游标。
            "skill_events": skill_events_after(
                window,
                after=after,
                state=session.engine.state,
                activations=session.engine.skill_activations,
                # 应答指向的询问可能早于本批游标 ⇒ 应答者表取**全量**那一份。
                query_actors=query_actors,
            ),
            # damage-fx 票 02：伤害事实是**第二条派生通道**，与 `skill_events` 同姿态——
            # 事实来源是引擎的只增缓冲 `Engine.damage_events`（票 01），共用同一个 `after`
            # 游标 ⇒ 轮询不重复下发。`HpChangeDelta` 里没有来源与属性，剑光的起 / 落点只能
            # 从这里读；伤害是公开事实（血条本来就公开），不按视角过滤。
            "damage_events": damage_events_after(
                window,
                after=after,
                state=session.engine.state,
                events=session.engine.damage_events,
            ),
            # chain-death-voices 票 03：卡牌使用 / 打出事实是**第三条派生通道**，与
            # `damage_events` 同姿态——事实来源是引擎的只增缓冲 `Engine.card_events`，
            # 共用同一个 `after` 游标 ⇒ 轮询不重复出声。记录面只有
            # `card_move(reason=USE)`，而"这次当成什么牌用掉"（转化 / 纯虚拟的产物牌型）
            # 与"使用者性别"都只能从这里读；使用 / 打出是公开事实，不按视角过滤。
            "card_events": card_events_after(
                window,
                after=after,
                state=session.engine.state,
                events=session.engine.card_events,
            ),
            # vital-fx 票 01：失去体力事实是**第四条派生通道**，与 `damage_events` 同姿态——
            # 事实来源是引擎的只增缓冲 `Engine.life_loss_events`，共用同一个 `after` 游标
            # ⇒ 轮询不重复下发。记录面分不出「失去体力」与「受到伤害」（判据是帧私有的
            # `LIFE_DECREASE_DAMAGE_KEY`，不进记录流），墨因此只能从这里读；体力是公开事实，
            # 不按视角过滤。排播 / 画面不在本票（票 02 / 03）。
            "life_loss_events": life_loss_events_after(
                window,
                after=after,
                state=session.engine.state,
                events=session.engine.life_loss_events,
            ),
            "session_log": [entry.as_dict() for entry in session.logs_after(logs_after)],
        }
        # 开局底稿（table-ux-overhaul 票 14）：**发牌前**那一帧的权威快照，只在
        # `after < 0`（首屏 / 建局那一次）随载荷下发。它不是记录流的一部分（不进
        # `records`、不是 delta），回放单独喂记录流时逐字节不受影响；前端拿它当开局批的
        # 回放底稿，不再从 post-init 快照倒推。其余批**不带这个键**（不是 `null`）。
        if after < 0:
            payload["setup"] = session.setup
        return payload

    # -- 叙事增量缓存（票 01） -----------------------------------------------

    def _narrative(
        self, session: Session, batch: RecordWindow, after: int
    ) -> list[dict[str, Any]]:
        """本批的叙事条目（票 01：只派生**新增的那一段**，不再整份重建再过滤）。

        缓存挂在会话自己身上（``Session.narrative_cursor``）：随会话一起建、一起丢，既不
        需要第二张注册表，也不会在回收会话时漏掉。首见 / 键（查看者·阵容·内容包）变了
        才喂**整条**记录流（保住轮次 / 阶段 / 亮牌帧这些跨批上下文），之后只喂尾部窗口。
        """
        cursor = session.narrative_cursor
        if not isinstance(cursor, NarrativeCursor) or not cursor.accepts(
            viewer=session.human_seat,
            heroes=session.hero_assignments,
            pack_id=session.pack.pack_id,
        ):
            cursor = NarrativeCursor(
                viewer=session.human_seat,
                heroes=session.hero_assignments,
                pack_id=session.pack.pack_id,
            )
            cursor.extend(batch.records)
            session.narrative_cursor = cursor
        else:
            cursor.extend(batch.tail)
        return cursor.entries_after(after)

    # -- 应答 ---------------------------------------------------------------

    def answer(self, session: Session, body: dict[str, Any], *, after: int, logs_after: int) -> dict[str, Any]:
        from sgs.state import parse_action

        raw = body.get("action")
        try:
            action = parse_action(raw)
        except Exception as error:  # pydantic ValidationError：结构非法
            raise ValueError(f"应答结构非法：{error}") from error
        session.submit_answer(action)
        self.advance_clock(session)
        return self.view(session, after=after, logs_after=logs_after)

    # -- 步骤预览（票 11：只读） ---------------------------------------------

    def preview(self, session: Session, body: dict[str, Any]) -> dict[str, Any]:
        """**只读**预览：这一步此刻的候选 / 还能点哪些槽位（票 11 落清单步，票 15 扩选牌面）。

        两种形状、同一份纪律：

        - **预发动清单一步**（body 带 ``skill_id`` + ``answers``）⇒ :meth:`_manifest_preview`。
          读的是与校验器**同一份**池 / 界回调（:func:`sgs.skills.flow.preview_step` 复用
          ``_with_answers`` + ``_seat_bounds`` / ``_resolve_options`` / ``_card_options``），
          故"预览说能点"与"提交会被接受"不可能各说一套。
        - **选牌面**（body 只带 ``picks``）⇒ :meth:`_pick_preview`：自家手牌面上"还要点哪
          几张才凑得出合法答案"由引擎的选牌枚举器现算，前端只读 ``disabled``。

        **只读全停**：不建询问、不写记录、不耗序号、不搬牌、不抽随机数（与
        :meth:`Engine.respond_candidates` 同一条纪律），也不碰 ``view()`` / 投影 / 快照
        ——预览是"每变一次选择问一次"的热路径，票面的性能底线就落在这条上。

        ``answers_seq`` 对不上当前挂起 ⇒ :class:`StaleAnswer`（409，与 ``/answer`` 同形：
        服务端唯一承认的"当前询问"仍是那一个）。**不**走 :meth:`advance_clock`：预览不能
        触发超时代答（那会改状态），只打一次"最后访问"戳（会话回收的判据）。
        """
        pending = session.pending
        answers_seq = body.get("answers_seq")
        if pending is None or answers_seq != pending.seq:
            raise StaleAnswer(pending, human_seat=session.human_seat)
        if "picks" in body:
            return self._pick_preview(session, body)
        return self._manifest_preview(session, body)

    def _manifest_preview(self, session: Session, body: dict[str, Any]) -> dict[str, Any]:
        """预发动清单**一步**的现算候选：能点谁 / 能不能进下一步 / 能不能提交（票 11）。

        行为与回包字段自票 11 起**逐字不变**（票 15 只把 ``preview`` 拆成两档，没动这一档）。
        """
        from sgs.skills import preview_activation

        pending = session.pending
        skill_id = body.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            raise ValueError("预览请求缺 skill_id")
        steps = issued_manifest(pending, skill_id)
        if not steps:
            raise ValueError(f"技能 {skill_id} 不在这次询问下发的预发动清单里")
        answers = _preview_answers(body.get("answers"))
        engine = session.engine
        # 承载上下文与校验器**同一条取法**（栈顶优先，帧外时机退回那一次的承载帧）：
        # 池回调读的帧必须与复合动作提交时那份一致，否则预览与裁决会各说一套。
        frame = engine.stack.top if engine.stack else engine.activation_frame
        try:
            preview = preview_activation(
                engine.state,
                pending.actor,
                skill_id,
                answers,
                steps,
                timing=pending.timing,
                frame=frame,
            )
        except KeyError as error:
            # 池回调要帧上下文而这一问没有 / 提供步给了不在池里的牌 id：都是"这份载荷此刻
            # 算不出来"⇒ 结构非法（400）。**不能**漏成 KeyError——那会被 `do_POST` 的
            # "未知会话"分支接走，回一个与真相无关的 404。
            raise ValueError(f"预览算不出这一步的候选：{error}") from error
        session.touch(time.monotonic())
        return {
            "cursor": preview.cursor,
            "kind": preview.kind,
            "options": list(preview.options),
            "disabled": list(preview.disabled),
            "can_advance": preview.can_advance,
            "can_submit": preview.can_submit,
            "next": preview.next,
        }

    def _pick_preview(self, session: Session, body: dict[str, Any]) -> dict[str, Any]:
        """选牌面**此刻还能点哪些槽位**（table-ux-overhaul 票 15；body 只带 ``picks``）。

        判据**不是**另一条规则：它读 :func:`sgs.effects.pick_completions`——与候选生成
        同一个枚举器（`effects/actions.py:select_card_picks`）——回答"给定已选的这几张，
        哪些槽位还出现在某个**合法补全**里"。故"预览说点得动"与"候选里真有那条"不可能
        各说一套：`max_per_suit` 这类候选生成期的收窄因此自动同源，前端不再自己数花色。

        三个字段：

        - ``disabled`` = 不在任何含当前 picks 的补全里的槽位。**已选的除外**：再点一下是
          取消，把它锁死会让玩家退不出这一组（服务端自己的候选也从不排除"再点一次"）。
        - ``can_submit`` = 当前 picks **本身**就是一条合法答案（= 提交会被接受）。
        - ``can_advance`` = 还补得下去（``False`` = 这组选择已是死路，得先退一张）。

        与 :meth:`_manifest_preview` 同一条只读纪律：不建询问、不写记录、不耗序号、
        不搬牌、不抽随机数，也不碰 ``view()`` / 投影 / 快照。
        """
        from sgs.effects import pick_completions

        pending = session.pending
        if str(getattr(pending, "kind", "")) != "SELECT_CARDS":
            raise ValueError("这一问不是选牌面，不能问选牌预览")
        if int(pending.actor) != int(session.human_seat):
            raise ValueError("选牌预览只服务应答者本人的选牌面")
        skill_id = body.get("skill_id")
        if isinstance(skill_id, str) and skill_id:
            # 两种形状各有各的必需键。混着发会让"缺 skill_id"与"缺 picks"两条 400 互相
            # 掩盖，前端也就分不清自己问的是哪一种。
            raise ValueError("选牌预览的载荷不带 skill_id（那是预发动清单预览的形状）")
        slots = tuple(getattr(pending, "slots", ()) or ())
        picks = _pick_slots(body.get("picks"), len(slots))
        completions = pick_completions(slots, pending.spec, picks)
        reachable = {index for combo in completions for index in combo}
        session.touch(time.monotonic())
        return {
            "disabled": [
                f"slot:{index}"
                for index in range(len(slots))
                if index not in picks and index not in reachable
            ],
            "can_submit": picks in completions,
            "can_advance": bool(completions),
        }

    # -- 终局落盘 -----------------------------------------------------------

    def save_if_finished(self, session: Session) -> Path | None:
        """终局后**只写一次**；已写过就不再写。

        幂等键取**写出的路径**而不是 ``session_id``——同一进程里可能有多个服务实例
        （测试就同时跑两个），而 ``session_id`` 是**每个注册表各自**从 ``s1`` 起编号的，
        用它当键会让两个服务互相顶掉对方的"已保存"标记。
        """
        if not session.finished:
            return None
        key = f"{self.runs_dir.resolve()}::{session.session_id}"
        if key in _SAVED:
            return None
        path = save_run(self.runs_dir, session)
        _SAVED.add(key)
        # perf-web 票 01：落盘成功才回收——**先保住文件、再丢内存**。按"落盘先后"记序，
        # 不按创建序：两局并行时先创建的那局可能后结束，按创建序会把它误当成"旧的"回收。
        self._finished_order.append(session.session_id)
        self._prune_finished()
        return path

    def _prune_finished(self) -> None:
        """回收较旧的已终局会话：保留最近 :data:`KEEP_FINISHED_SESSIONS` 局 + 全部未终局。"""
        while len(self._finished_order) > KEEP_FINISHED_SESSIONS:
            oldest = self._finished_order.pop(0)
            self._drop_session(oldest)

    def _prune_abandoned(self, now: float) -> None:
        """回收"久未访问且未终局"的会话：保留最近 :data:`KEEP_LIVE_SESSIONS` 局（票 01）。

        "最近"按 :attr:`Session.last_access`（GET / answer / create 三条入口都会打戳）：
        最近那几局**永不**按空闲回收——同一进程里可能还有页面在轮询；更旧的局只有空闲超过
        :data:`ABANDONED_SESSION_SECONDS` 才收。未落盘的**已终局**会话不在本函数的回收面内
        （它们马上会被 :meth:`save_if_finished` 写成文件，丢了就真丢了）。
        """
        live = [session for session in self.sessions.values() if not session.finished]
        if len(live) <= KEEP_LIVE_SESSIONS:
            return
        live.sort(key=lambda session: session.last_access, reverse=True)
        for session in live[KEEP_LIVE_SESSIONS:]:
            if not session.last_access:  # 直接构造、没打过戳的会话：无从判"久未访问"
                continue
            if now - session.last_access < ABANDONED_SESSION_SECONDS:
                continue
            self._drop_session(session.session_id)

    def _drop_session(self, session_id: str) -> None:
        """把一局从内存里摘掉（会话本身带着自己的叙事缓存，故这里只有一张表要清）。"""
        self.sessions.pop(session_id, None)


class _Handler(BaseHTTPRequestHandler):
    """每个请求一个实例（``http.server`` 的约定）；状态都在 :class:`GameRegistry` 里。"""

    registry: GameRegistry
    quiet: bool = False
    server_version = "sgs-web/0.1"

    # -- 工具 ---------------------------------------------------------------

    def _send_json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"请求体不是合法 JSON：{error}") from error
        if not isinstance(parsed, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return parsed

    def _after(self, query: dict[str, list[str]]) -> int:
        return int(query.get("after", ["-1"])[0])

    def _logs_after(self, query: dict[str, list[str]]) -> int:
        return int(query.get("logs", ["0"])[0])

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 基类签名
        """把访问日志压到一行（结构化侧车日志在会话里，不在这里）。

        ``quiet=True``（``serve.py --quiet``）时**整条丢弃**：后台验收任务把服务输出
        收进日志时，逐请求的 GET 行没有任何信息量，只会把真正的启动 / 报错行淹掉。
        """
        if self.quiet:
            return
        print(f"[sgs-web] {self.address_string()} {format % args}", flush=True)

    # -- 路由 ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - 基类签名
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/runs":
                self._send_json(200, {"runs": list_runs(self.registry.runs_dir)})
                return
            if parsed.path == "/api/heroes":
                self._send_json(200, {"heroes": hero_catalog()})
                return
            if parsed.path == "/api/hero-presets":
                self._send_json(200, {"presets": self.registry.hero_presets()})
                return
            if parsed.path == "/api/decks":
                self._send_json(200, {"decks": deck_catalog()})
                return
            if parsed.path.startswith("/api/runs/"):
                name = parsed.path[len("/api/runs/") :]
                self._send_json(200, load_run(self.registry.runs_dir, name))
                return
            if parsed.path.startswith("/api/sessions/"):
                session = self.registry.get(parsed.path[len("/api/sessions/") :])
                self.registry.advance_clock(session)
                self.registry.save_if_finished(session)
                self._send_json(
                    200,
                    self.registry.view(session, after=self._after(query), logs_after=self._logs_after(query)),
                )
                return
        except KeyError:
            self._send_json(404, {"error": "未知会话"})
            return
        except (ValueError, OSError) as error:
            self._send_json(400, {"error": str(error)})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - 基类签名
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            body = self._read_body()
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        try:
            if parsed.path == "/api/hero-presets":
                self._send_json(200, {"presets": self.registry.save_hero_presets(body)})
                return
            if parsed.path == "/api/sessions":
                session = self.registry.create(body)
                self._send_json(200, self.registry.view(session, after=-1, logs_after=0))
                return
            if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/preview"):
                session_id = parsed.path[len("/api/sessions/") : -len("/preview")]
                self._send_json(200, self.registry.preview(self.registry.get(session_id), body))
                return
            if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/answer"):
                session_id = parsed.path[len("/api/sessions/") : -len("/answer")]
                session = self.registry.get(session_id)
                payload = self.registry.answer(
                    session, body, after=self._after(query), logs_after=self._logs_after(query)
                )
                self.registry.save_if_finished(session)
                self._send_json(200, payload)
                return
        except KeyError:
            self._send_json(404, {"error": "未知会话"})
            return
        except StaleAnswer as error:
            # 这份"当前挂起"是给人看的：查看者（这个会话的人类座位）随异常带出来，才能判
            # "该不该带观看来的牌面"（票 15 私密观看 / 评审 finding ①）。
            self._send_json(
                409,
                {
                    "error": str(error),
                    "pending": (
                        pending_payload(error.pending, human_seat=error.human_seat)
                        if error.pending is not None
                        else None
                    ),
                },
            )
            return
        except IllegalActionError as error:
            self._send_json(400, {"error": str(error), "reason_code": error.reason_code})
            return
        except DataLoadError as error:
            # 未知牌堆档位等加载期错误：400。建局是"先加载内容包、后登记会话"，
            # 故这条路径不会留下半局（D15 的"结构非法不改状态"同理）。
            self._send_json(400, {"error": str(error)})
            return
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
            return
        self._send_json(404, {"error": "未知路径"})

    # -- 静态资源 -----------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in target.parents and target != STATIC_ROOT.resolve():
            self._send_json(404, {"error": "越界路径"})
            return
        if not target.is_file():
            # 单页应用：未知的根路径都回落到 index.html（回放页也在这里）。
            target = STATIC_ROOT / "index.html"
            if not target.is_file():
                self._send_json(404, {"error": f"静态资源缺失：{relative}"})
                return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


DEFAULT_HOST = "127.0.0.1"
"""缺省只监听回环：单机自用最安全，也不给同网段任何一台机器开门。

WSL2 里浏览器跑在 Windows 那侧时，回环**可能**被 localhost 转发接上，但不保证；
那时候用 ``--host 0.0.0.0`` 让 Windows 走 WSL 的 IP（见 `map.md` §Notes 的"跨 WSL 打开界面"）。
"""


def make_server(
    port: int = DEFAULT_PORT,
    runs_dir: Path | None = None,
    host: str = DEFAULT_HOST,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    """造一个已装好路由的 HTTP 服务；``port=0`` 让内核分配（``server_address[1]`` 读回）。

    ``quiet=True`` 把"关访问日志"这件事挂在**绑定后的处理器类**上：``_Handler.log_message``
    只认 ``self.quiet``，故每个请求实例都看得见，不用改 ``ThreadingHTTPServer``。
    """
    registry = GameRegistry(runs_dir=runs_dir)
    handler = type("_BoundHandler", (_Handler,), {"registry": registry, "quiet": quiet})
    server = ThreadingHTTPServer((host, port), handler)
    server.registry = registry  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    """``python tools/sgs_web/serve.py [--host H] [--port N] [--runs-dir DIR] [--quiet]``。

    启动行（``sgs-web listening on …``）与 ``--quiet`` 无关：它由 ``main`` 打到 stdout，
    后台任务仍照旧从这一行读端口。``--quiet`` 只关**逐请求**的访问日志。
    """
    import argparse

    parser = argparse.ArgumentParser(prog="sgs-web", description="SGS 极简 Web 界面（M4）。")
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"监听地址（缺省 {DEFAULT_HOST}）；想让 WSL 之外的浏览器访问就用 0.0.0.0。",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口；0 = 由内核分配。")
    parser.add_argument(
        "--runs-dir", type=Path, default=DEFAULT_RUNS_DIR, help="录像目录（缺省仓根 runs/）。"
    )
    parser.add_argument(
        "--quiet",
        "--no-access-log",
        dest="quiet",
        action="store_true",
        help="不打印逐请求的访问日志（启动行照旧）；后台 / 验收任务用。",
    )
    args = parser.parse_args(argv)

    server = make_server(args.port, args.runs_dir, host=args.host, quiet=args.quiet)
    host, port = server.server_address[:2]
    print(f"sgs-web listening on http://{host}:{port}/  (runs: {args.runs_dir})", flush=True)
    if host == "0.0.0.0":  # noqa: S104 - 显式选项，不是缺省
        print(
            "提示：0.0.0.0 = 监听全部网卡。WSL 里从 Windows 打开请用 "
            "「在 PowerShell 里跑 wsl hostname -I 拿到的那个 IP」:端口。",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
