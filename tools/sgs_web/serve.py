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
``GET``          ``/api/heroes``                     开局页点将列表（启用池的 id + 名称）
``GET``          ``/api/decks``                      开局页牌堆档位列表（内容包 decks 的键 + 文案）
``GET``          ``/api/runs``                       录像列表
``GET``          ``/api/runs/<name>``                某局录像（meta + 记录流）
===============  ==================================  ==================================

**一次请求最多推进到下一个暂停点或终局**（D16，由 :meth:`Session.drive` 保证）。
**过期应答 409、结构非法 400、未知会话 404，三种都不写记录、不改状态**（D15）。
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):  # pragma: no cover - 直接 `python tools/sgs_web/serve.py` 时
    # 脚本直跑：把 ``tools/`` 放进 ``sys.path``，让 ``sgs_web`` 成为一个可导入的顶层包。
    # （相对导入在"脚本直跑"下不成立——那时连父包都没有。）
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sgs_web import REPO_ROOT, ensure_engine_importable

ensure_engine_importable()

from sgs.data import DataLoadError, enabled_hero_ids, load_content_pack, load_heroes  # noqa: E402
from sgs.engine import IllegalActionError  # noqa: E402
from sgs_web.narrative import narrative_after  # noqa: E402
from sgs_web.projection import (  # noqa: E402
    build_skills,
    pending_payload,
    skill_events_after,
    visible_record,
    visible_snapshot,
)
from sgs_web.replay import list_runs, load_run, save_run  # noqa: E402
from sgs_web.session import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    Session,
    StaleAnswer,
    create_session,
)
from sgs_web.timeout import fallback_action  # noqa: E402

__all__ = ["GameRegistry", "deck_catalog", "hero_catalog", "make_server", "main"]

STATIC_ROOT = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8100
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


_SAVED: set[str] = set()
"""已经落过盘的 ``<录像目录>::<会话 id>``；**只写一次**的幂等键（地图 D14）。

键里带上录像目录，是因为同一进程可以有多个服务实例（测试就同时跑两个），
而会话 id 是每个注册表各自从 ``s1`` 起编号的。
"""


def hero_catalog() -> list[dict[str, str]]:
    """开局页点将列表的只读面（票 19）：**启用池**的 ``id`` + 名称，按数据文件顺序。

    池的来源与发放段**同源**（``enabled_hero_ids`` 读同一个 ``enabled`` 字段），
    所以"列表里点得到的"与"引擎发得出的"不会各抄一份名单而漂开。
    """
    table = load_heroes()
    catalog: list[dict[str, str]] = []
    for hero_id in enabled_hero_ids(table):
        hero = table.by_id(hero_id)
        assert hero is not None  # enabled_hero_ids 只从这张表里挑，找不到就是上游违约
        catalog.append({"id": hero.id, "name": hero.name})
    return catalog


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


class GameRegistry:
    """内存里的会话表；**一局一个会话**，进程重启即丢（D15、`06` §2"单机跑通再谈"）。"""

    def __init__(self, runs_dir: Path | None = None) -> None:
        self.sessions: dict[str, Session] = {}
        self.runs_dir = Path(runs_dir) if runs_dir is not None else DEFAULT_RUNS_DIR
        self._counter = 0

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
            timeout_seconds=body.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            pack=pack,
            session_id=f"s{self._counter}",
        )
        session.mark_pending(time.monotonic())
        self.sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    # -- 取增量（**超时判定的唯一触发点**） ---------------------------------

    def advance_clock(self, session: Session) -> None:
        """到点就代答，可能连续代答多次（超时后引擎又停在同一个人类的下一问）。"""
        now = time.monotonic()
        session.mark_pending(now)
        while session.expire_if_due(time.monotonic(), fallback_action):
            pass

    def view(self, session: Session, *, after: int, logs_after: int) -> dict[str, Any]:
        """会话视角的响应体（建局 / 取增量 / 应答三处**共用同一个形状**）。"""
        all_records = session.records()
        records = [
            payload
            for record in session.records_after(after)
            if (payload := visible_record(record, viewer=session.human_seat)) is not None
        ]
        return {
            "session_id": session.session_id,
            "seed": session.seed,
            "player_count": session.rule_config.player_count,
            "human_seat": session.human_seat,
            "heroes": list(session.hero_assignments),
            "deck_profile": session.pack.deck_profile,
            "timeout_seconds": session.timeout_seconds,
            "pending_since": session.pending_since,
            "finished": session.finished,
            "cursor": session.next_cursor(after),
            "log_seq": session.log_seq,
            "snapshot": visible_snapshot(session),
            "skills": build_skills(session, session.human_seat),
            "pending": pending_payload(session),
            "records": records,
            # U1-05 叙事契约：简易事件流的条目由服务端生成、按查看者过滤；前端只渲染。
            # 游标复用记录流的 `after`——条目自带产生它的记录 `seq`，两批拼接 = 全量。
            "narrative": narrative_after(
                all_records,
                after=after,
                viewer=session.human_seat,
                heroes=session.hero_assignments,
                pack_id=session.pack.pack_id,
            ),
            # U3-04 / D8.2：技能发动是**派生通道**，不是记录流的新事实——从**未裁剪**的
            # 记录里取「已接受、kind = ActivateSkill」的那几条，补上被 `visible_record`
            # 裁掉的 skill_id / choices；与 `records` 共用同一个 `after` 游标。
            "skill_events": skill_events_after(
                all_records, after=after, state=session.engine.state
            ),
            "session_log": [entry.as_dict() for entry in session.logs_after(logs_after)],
        }

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
        return path


class _Handler(BaseHTTPRequestHandler):
    """每个请求一个实例（``http.server`` 的约定）；状态都在 :class:`GameRegistry` 里。"""

    registry: GameRegistry
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
        """把访问日志压到一行（结构化侧车日志在会话里，不在这里）。"""
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
            if parsed.path == "/api/sessions":
                session = self.registry.create(body)
                self._send_json(200, self.registry.view(session, after=-1, logs_after=0))
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
    port: int = DEFAULT_PORT, runs_dir: Path | None = None, host: str = DEFAULT_HOST
) -> ThreadingHTTPServer:
    """造一个已装好路由的 HTTP 服务；``port=0`` 让内核分配（``server_address[1]`` 读回）。"""
    registry = GameRegistry(runs_dir=runs_dir)
    handler = type("_BoundHandler", (_Handler,), {"registry": registry})
    server = ThreadingHTTPServer((host, port), handler)
    server.registry = registry  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    """``python tools/sgs_web/serve.py [--host H] [--port N] [--runs-dir DIR]``；把实际地址打到 stdout。"""
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
    args = parser.parse_args(argv)

    server = make_server(args.port, args.runs_dir, host=args.host)
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
