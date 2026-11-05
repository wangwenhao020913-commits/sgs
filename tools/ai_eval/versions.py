"""旧启发式版本的加载器（票 13；spec §2.6）。

``git show <tag>:src/sgs/agents/heuristic.py`` → 落到 gitignored 的
``runs/ai-versions/<tag>.py`` → ``importlib`` 以**独立模块名**加载。旧版只依赖
``sgs.effects`` / ``sgs.state`` 的公开面，所以它解析到**当前**引擎——不 vendored、不涨仓库
体积。

三条硬断言（spec §2.6 的"不许静默打回 HEAD"）：

1. ``git show`` 失败（缺 tag / 不是 git 仓）⇒ :class:`~ai_eval.errors.AiVersionError`
   **点名 ref**，不存在任何"回退 HEAD"的分支；
2. 落盘的副本逐字节等于 ``git show`` 读到的字节（写完**读回**再比，写坏了就红）；
3. ``__file__`` 指向 ``runs/ai-versions/`` 的副本——不是主树的
   ``src/sgs/agents/heuristic.py``。

``.venv`` 是 editable 指向主树，``.pth`` 会让"以为在打旧版"静默成立（spec §2.6 的现场
证据），故加载**只走副本**：同一 ref 只有一条路径，``import sgs.agents`` 从不参与。
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import AiVersionError

__all__ = [
    "DEFAULT_VERSIONS_DIR",
    "HEURISTIC_PATH",
    "LoadedVersion",
    "git_commit",
    "load_version",
    "repository_root",
]

HEURISTIC_PATH = "src/sgs/agents/heuristic.py"
"""被冻结 / 被加载的唯一模块（spec §2.6）。"""

DEFAULT_VERSIONS_DIR = Path("runs") / "ai-versions"
"""gitignored 的副本目录（与 ``runs/`` 同一条 gitignore）。"""

MODULE_PREFIX = "_sgs_ai_version_"
"""独立模块名前缀：不覆盖 ``sgs.agents.heuristic``，两版可同进程并存。"""


def repository_root() -> Path:
    """本工具所在仓库的根（``tools/ai_eval/versions.py`` → 上两级）。"""
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LoadedVersion:
    """一个可用的旧版启发式：ref、提交、字节摘要、副本路径、参与者类。"""

    ref: str
    commit: str
    heuristic_sha256: str
    path: Path
    player_class: type

    def to_dict(self) -> dict[str, str]:
        """§2.8 里 ``{ref, heuristic_sha256}`` 的写法（``ref`` 用解析后的提交）。"""
        return {"ref": self.commit, "heuristic_sha256": self.heuristic_sha256}


_CACHE: dict[tuple[str, str, str], LoadedVersion] = {}
"""进程内缓存：同一 (仓库, 副本目录, ref) 只读一次 git、只 import 一次。

worker 进程用 ``fork`` 起（``sgs_batch.parallel``），故父进程加载过的版本直接继承。
"""


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as error:  # 没有 git 可执行文件
        raise AiVersionError(f"找不到 git 可执行文件（要读 {args}）：{error}") from error


def _failure_detail(completed: subprocess.CompletedProcess[bytes]) -> str:
    text = (completed.stderr or completed.stdout or b"").decode("utf-8", "replace").strip()
    return text.splitlines()[0] if text else f"git 退出码 {completed.returncode}"


def git_commit(ref: str, *, repo_root: Path | None = None) -> str:
    """把 ref 解析成完整提交 sha；解析失败即点名 ref 响亮失败。"""
    root = Path(repo_root) if repo_root is not None else repository_root()
    completed = _run_git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if completed.returncode != 0:
        raise AiVersionError(f"解析不了版本 {ref!r}：{_failure_detail(completed)}")
    return completed.stdout.decode("utf-8").strip()


def _git_show(ref: str, root: Path) -> bytes:
    completed = _run_git(root, "show", f"{ref}:{HEURISTIC_PATH}")
    if completed.returncode != 0:
        raise AiVersionError(
            f"读不到版本 {ref!r} 的 {HEURISTIC_PATH}（不静默回退 HEAD）："
            f"{_failure_detail(completed)}"
        )
    return completed.stdout


def _safe_name(ref: str) -> str:
    """ref → 文件名：只留 ``[A-Za-z0-9._-]``，防 ``../`` 越界（git ref 也不许这些）。"""
    cleaned = "".join(
        character if character.isalnum() or character in "._-" else "_" for character in ref
    ).strip("._")
    return cleaned or "version"


def _write_copy(path: Path, data: bytes) -> None:
    """写副本（独立函数：测试注入"写坏一个字节"的合成反例）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _import_copy(path: Path, *, ref: str, digest: str) -> object:
    """以独立模块名从副本 import；``__file__`` 必须指回副本。"""
    name = f"{MODULE_PREFIX}{_safe_name(ref)}_{digest[:8]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AiVersionError(f"版本 {ref!r} 的副本无法 import：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # noqa: BLE001 - 旧版加载失败要点名 ref 与原因
        raise AiVersionError(
            f"版本 {ref!r} 加载失败（{path}）：{type(error).__name__}: {error}"
        ) from error
    module_file = getattr(module, "__file__", None)
    if module_file is None or Path(module_file).resolve() != path.resolve():
        raise AiVersionError(
            f"版本 {ref!r} 的 __file__ 没有指向 runs 副本：{module_file!r} != {str(path)!r}"
        )
    return module


def load_version(
    ref: str,
    *,
    repo_root: Path | None = None,
    versions_dir: Path | None = None,
) -> LoadedVersion:
    """加载 ref 指向的启发式模块（缺 tag / 字节不一致 / 没有参与者类都响亮失败）。"""
    if not isinstance(ref, str) or not ref.strip():
        raise AiVersionError(f"版本 ref 必须是非空字符串，得到 {ref!r}")
    root = (Path(repo_root) if repo_root is not None else repository_root()).resolve()
    target_dir = (
        Path(versions_dir) if versions_dir is not None else root / DEFAULT_VERSIONS_DIR
    ).resolve()
    key = (str(root), str(target_dir), ref)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    data = _git_show(ref, root)
    digest = hashlib.sha256(data).hexdigest()
    commit = git_commit(ref, repo_root=root)
    path = target_dir / f"{_safe_name(ref)}.py"
    _write_copy(path, data)
    written = path.read_bytes()
    if written != data:
        raise AiVersionError(
            f"版本 {ref!r} 的副本与 git show 字节不一致（{path}）："
            f"sha256={hashlib.sha256(written).hexdigest()} != {digest}"
        )
    module = _import_copy(path, ref=ref, digest=digest)
    player_class = getattr(module, "HeuristicPlayer", None)
    if player_class is None:
        raise AiVersionError(f"版本 {ref!r} 的 {HEURISTIC_PATH} 没有导出 HeuristicPlayer")
    loaded = LoadedVersion(
        ref=ref,
        commit=commit,
        heuristic_sha256=digest,
        path=path,
        player_class=player_class,
    )
    _CACHE[key] = loaded
    return loaded
