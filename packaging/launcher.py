#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SGS 便携版启动器：**双击进网页，带参数走命令行**。

这个文件是 exe 的唯一入口（`packaging/SGS.spec` 的 `Analysis` 只喂它一个脚本）。
它**不碰引擎、不碰接入层**，只走两条既有的公开缝：

* 网页模式 → :func:`sgs_web.serve.make_server`（`serve.py` 的公开函数）；
* 命令行模式 → :func:`sgs.cli.main`（`[project.scripts]` 那条入口）。

四条设计约束（决策记录见 `packaging/README.md`）：

1. **数据落点**：`%LOCALAPPDATA%\\SGS\\`（非 Windows 退到 `~/.local/share/SGS`）。
   源码树里 `serve.py` 的缺省 runs 目录是从 `__file__` 推出来的（`REPO_ROOT / "runs"`），
   在冻结包里会指到 PyInstaller 的解包目录 / 安装目录，故这里**显式**把
   `runs_dir` 传进 `make_server()`——不改源码一个字。
2. **单实例**：`server.json` 记 `{port, pid, url}`；启动前先 **HTTP 探活**，已在跑就只开
   浏览器后退出。**探活走 HTTP 不走 pid**：进程崩掉后 pid 会被系统复用、文件也会假活。
3. **控制台**：stdout 同时写 `server.log`（UTF-8，超过 2 MB 滚一份 `.log.1`）。
   Windows 控制台里 Python 用 `WriteConsoleW` 写 stdout，故中文不会乱码；重定向到文件时
   才按本地编码走，故日志单独用 UTF-8 落盘。
4. **退出**：关掉控制台窗口 = 进程被杀（`http.server` 会随进程一起没）；`Ctrl+C` 走
   `KeyboardInterrupt` → `server_close()` → 清掉 `server.json`。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Final

APP_NAME: Final = "SGS"
APP_TITLE: Final = "SGS · 三国杀（便携版）"
DEFAULT_PORT: Final = 8100
PORT_FALLBACK_SPAN: Final = 9
"""缺省端口被占时往后顺延几格（8101–8109），再不行才交给内核。"""

DEFAULT_HOST: Final = "127.0.0.1"

PROBE_TIMEOUT: Final = 1.5
"""探活超时（秒）：本机回环，1.5 s 已经远超正常响应。"""

BROWSER_DELAY: Final = 0.4
"""开浏览器比 `serve_forever()` 晚一点点，避免"浏览器先到、服务还没进循环"。"""

LOG_NAME: Final = "server.log"
STATE_NAME: Final = "server.json"
LOG_MAX_BYTES: Final = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _bundle_root() -> Path:
    """冻结包内 `--add-data` 的落点（onedir 下是 `_internal/`）；源码态是仓根。"""
    if _frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _data_root() -> Path:
    """可写的用户数据目录：`%LOCALAPPDATA%\\SGS`（非 Windows 走 XDG）。"""
    override = os.environ.get("SGS_HOME")
    if override:
        target = Path(override).expanduser()
    else:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            target = Path(local) / APP_NAME
        else:
            base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
            target = Path(base) / APP_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _ensure_import_paths() -> None:
    """源码态直跑时把 `src/` 与 `tools/` 塞进 `sys.path`（冻结包里已由 spec 收进字节码）。"""
    if _frozen():
        return
    root = _bundle_root()
    for entry in (root / "src", root / "tools"):
        text = str(entry)
        if text not in sys.path:
            sys.path.insert(0, text)


def _build_info() -> dict[str, Any]:
    """构建戳（`build.ps1` 在打包前写进 staging 树的 `build-info.json`）；源码态返回空。"""
    path = _bundle_root() / "build-info.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _version_line() -> str:
    info = _build_info()
    parts = [
        f"SGS {info.get('version') or 'dev'}",
        f"Python {info.get('python') or sys.version.split()[0]}",
    ]
    if info.get("built_at"):
        parts.append(f"构建于 {info['built_at']}")
    return "  ·  ".join(parts)


# ---------------------------------------------------------------------------
# 控制台 / 日志
# ---------------------------------------------------------------------------


class _Tee:
    """把 stdout 同时写进日志文件；其余属性一律透传给真 stdout。"""

    def __init__(self, stream: Any, handle: Any) -> None:
        self._stream = stream
        self._handle = handle

    def write(self, text: str) -> int:
        self._stream.write(text)
        self._handle.write(text)
        self._handle.flush()
        return len(text)

    def flush(self) -> None:
        self._stream.flush()
        self._handle.flush()

    def isatty(self) -> bool:
        checker = getattr(self._stream, "isatty", None)
        return bool(checker()) if callable(checker) else False

    def fileno(self) -> int:
        return self._stream.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._stream, name)


def _open_log(root: Path) -> Any:
    """打开（或滚动后打开）日志；失败就退化成"不记日志"，绝不因为日志起不来而不启动。"""
    path = root / LOG_NAME
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            backup = path.with_name(LOG_NAME + ".1")
            backup.unlink(missing_ok=True)
            os.replace(path, backup)
    except OSError:
        pass
    try:
        handle = open(path, "a", encoding="utf-8", buffering=1, errors="replace")
    except OSError:
        return None
    handle.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} 启动 =====\n")
    return handle


def _say(text: str = "") -> None:
    print(text, flush=True)


# ---------------------------------------------------------------------------
# 单实例状态
# ---------------------------------------------------------------------------


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("port"), int):
        return None
    return payload


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(STATE_NAME + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _clear_state(path: Path, port: int) -> None:
    """只删**自己那一条**：另一个实例后来居上时，别把它的状态文件删掉。"""
    current = _read_state(path)
    if current is None or current.get("port") == port:
        try:
            path.unlink()
        except OSError:
            pass


def _probe(port: int) -> str | None:
    """HTTP 探活：活着且**确实是 SGS** 才返回它的 url，否则 None。

    为什么不只看 200：端口会被系统/别的程序复用。`/api/decks` 的响应体第一键就是
    `"decks"`，用这个键当"是我们家的服务"的凭证，比只认状态码稳得多。
    """
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/api/decks"
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as response:  # noqa: S310 - 本机回环
            if response.status != 200:
                return None
            body = response.read(8192)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if b'"decks"' not in body:
        return None
    return f"http://127.0.0.1:{port}/"


def _open_browser(url: str) -> None:
    try:
        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 浏览器起不来不该拖垮服务
        opened = False
    if not opened:
        _say(f"没能自动打开浏览器，请手动访问：{url}")


# ---------------------------------------------------------------------------
# 网页模式
# ---------------------------------------------------------------------------


def _parse(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    """只认**启动器自己**的开关；其余参数整包交给 `sgs.cli`（命令行模式）。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="SGS",
        add_help=False,
        allow_abbrev=False,
        description="SGS 便携版：无参数 = 起网页；带 --players/--ai/--seed 等 = 在控制台跑一局。",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（缺省 {DEFAULT_PORT}）")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址（缺省只回环）")
    # argparse 会对 help 文本做 `%` 格式化，故这里的 `%` 必须写成 `%%`。
    parser.add_argument("--runs-dir", type=Path, default=None, help="录像目录（缺省 %%LOCALAPPDATA%%\\SGS\\runs）")
    parser.add_argument("--no-browser", action="store_true", help="不要自动打开浏览器")
    parser.add_argument("--quiet", action="store_true", help="不打印逐请求访问日志")
    parser.add_argument("--cli", action="store_true", help="强制命令行模式（其余参数原样交给 sgs.cli）")
    parser.add_argument("-h", "--help", action="store_true", help="显示本帮助")
    parser.add_argument("--version", action="store_true", help="显示版本")
    ns, unknown = parser.parse_known_args(argv)
    return vars(ns), unknown


def _help_text() -> str:
    return "\n".join(
        [
            APP_TITLE,
            "",
            "用法：",
            "  SGS.exe                        起本地服务并打开浏览器",
            "  SGS.exe --port 8123            指定端口（占用则自动换一个）",
            "  SGS.exe --no-browser           只起服务，不自动开浏览器",
            "  SGS.exe --players 5 --ai heuristic",
            "                                 命令行跑一局（参数见 SGS.exe --cli --help）",
            "  SGS.exe --version              显示版本",
            "",
            f"数据目录：{_data_root()}",
            "关闭控制台窗口即退出服务。",
        ]
    )


def _run_cli(argv: list[str]) -> int:
    from sgs.cli import main as cli_main

    return int(cli_main(argv))


def _bind(opts: dict[str, Any], runs_dir: Path) -> tuple[Any, int]:
    """先试指定端口，被占就顺延几个（保持"记住的地址"还有效），最后才交给内核分配。

    为什么不是"被占就直接内核分配"：Windows 上 8100 会被 WSL 的端口转发、Hyper-V 的保留
    区间之类的东西占着，而内核分配的 5 万号端口每次启动都不一样——用户收藏的书签就废了。
    顺延几个号能让地址**基本稳定**。
    """
    from sgs_web.serve import make_server

    host = str(opts["host"])
    quiet = bool(opts["quiet"])
    wanted = int(opts["port"])
    if wanted == 0:
        server = make_server(port=0, runs_dir=runs_dir, host=host, quiet=quiet)
        return server, int(server.server_address[1])

    candidates = [wanted + step for step in range(PORT_FALLBACK_SPAN + 1)]
    reason = ""
    for port in candidates:
        try:
            server = make_server(port=port, runs_dir=runs_dir, host=host, quiet=quiet)
        except OSError as error:
            reason = error.strerror or str(error)
            continue
        if port != wanted:
            _say(f"端口 {wanted} 起不来（{reason}），改用 {port}。")
        return server, int(server.server_address[1])

    _say(f"端口 {wanted}–{candidates[-1]} 都起不来（{reason}），交给系统分配。")
    server = make_server(port=0, runs_dir=runs_dir, host=host, quiet=quiet)
    return server, int(server.server_address[1])


def _run_web(opts: dict[str, Any]) -> int:
    root = _data_root()
    runs_dir = Path(opts["runs_dir"]).expanduser() if opts["runs_dir"] else root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    handle = _open_log(root)
    if handle is not None:
        sys.stdout = _Tee(sys.stdout, handle)
        # stderr 也要进日志：崩溃栈默认走 stderr，用户"把 server.log 发回来"才有料。
        sys.stderr = _Tee(sys.stderr, handle)

    state = root / STATE_NAME
    running = _read_state(state)
    if running is not None:
        url = _probe(int(running["port"]))
        if url is not None:
            _say(f"已经有一个 SGS 在跑了：{url}")
            if opts["no_browser"]:
                _say("不再开第二个实例，直接帮你打开浏览器（这次按 --no-browser 跳过）。")
            else:
                _say("不再开第二个实例，直接为你打开浏览器。")
                _open_browser(url)
            return 0

    server, port = _bind(opts, runs_dir)
    shown = "127.0.0.1" if str(opts["host"]) in ("0.0.0.0", "::") else str(opts["host"])
    url = f"http://{shown}:{port}/"

    _write_state(
        state,
        {
            "port": port,
            "pid": os.getpid(),
            "url": url,
            "runs_dir": str(runs_dir),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    _say("=" * 62)
    _say(f" {APP_TITLE}")
    _say(f" {_version_line()}")
    _say("=" * 62)
    _say(f" 地址：{url}")
    _say(f" 数据：{root}")
    _say(f" 日志：{root / LOG_NAME}")
    if str(opts["host"]) in ("0.0.0.0", "::"):
        _say(" 注意：已监听全部网卡，同网段的机器也能访问。")
    _say(" 关闭本窗口即退出游戏服务。")
    _say("=" * 62)

    if not opts["no_browser"]:
        timer = threading.Timer(BROWSER_DELAY, _open_browser, args=(url,))
        timer.daemon = True
        timer.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _say("\n收到 Ctrl+C，正在退出……")
    finally:
        server.server_close()
        _clear_state(state, port)
        _say("服务已停止。")
        if handle is not None:
            handle.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    opts, unknown = _parse(argv)

    if opts["version"]:
        _say(_version_line())
        return 0
    if opts["help"] and not unknown:
        _say(_help_text())
        return 0
    if opts["cli"] or unknown:
        _ensure_import_paths()
        return _run_cli(unknown)
    _ensure_import_paths()
    try:
        return _run_web(opts)
    except Exception:  # noqa: BLE001 - 双击场景：宁可停住让人看见，也不要窗口一闪就没
        import traceback

        traceback.print_exc()
        _say(f"\n启动失败。日志：{_data_root() / LOG_NAME}")
        _pause()
        return 1


def _pause() -> None:
    """双击启动失败时别让窗口一闪就没：等用户按一下回车。"""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return
        input("\n按回车关闭此窗口……")
    except (EOFError, OSError, KeyboardInterrupt):
        pass


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
