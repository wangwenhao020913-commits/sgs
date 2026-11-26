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

**兼容层（票 36）**：副本的选路表可能**不认识**它打 tag 之后才进仓的 ``QueryKind``
（票 32 的 446 errors：``ai-v10`` / ``ai-v0`` 都没有 ``SELECT_OPTIONS`` 这一行）。三条硬
断言一条不动——兼容层只在**加载侧**：:func:`~ai_eval.compat.capability_gap_kinds` 读出
缺哪几类，:func:`~ai_eval.compat.fail_closed_player_class` 把这些类型的应答换成**合法
放弃**（见 `ai_eval.compat` 的模块 docstring）。``LoadedVersion`` 因此另记两件事：
``raw_player_class``（副本原样的类，红-绿对照的"红"侧）与 ``capability_gap_kinds``
（版本级静态缺口，驱动侧据它数 ``capability_gaps``）。分发表完整的版本（当前 ``HEAD``）
``player_class is raw_player_class``——零包装、行为逐值不变。

**权重冻结（票 15）**：除 ``heuristic.py`` 外再冻结 ``src/sgs/agents/weights_data.py``——原始
字节落到 ``runs/ai-versions/<safe>-weights.py``，读回比对 sha256。ref 里没有这份文件（08b 的
结构件之前）⇒ **显式空表**（B 表 0 行）+ ``weights_frozen=False``，**不读主树的拟合值**。
副本的 ``from . import weights as weight_policy`` 在 ``exec_module`` 期间解析到**版本局部
代理**（:func:`sgs.agents.weights.local_policy`：冻结的 B 表 + 同样的读口），加载完立即把
``sys.modules`` 与包属性还原；主树 ``sgs.agents.weights`` 一个字段都不改，两版可同进程并存。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from sgs.agents import weights as weight_models

from .compat import capability_gap_kinds, fail_closed_player_class
from .errors import AiVersionError

__all__ = [
    "DEFAULT_VERSIONS_DIR",
    "HEURISTIC_PATH",
    "LoadedVersion",
    "WEIGHTS_PATH",
    "git_commit",
    "load_version",
    "repository_root",
]

HEURISTIC_PATH = "src/sgs/agents/heuristic.py"
"""被冻结 / 被加载的唯一模块（spec §2.6）。"""

WEIGHTS_PATH = "src/sgs/agents/weights_data.py"
"""随 ``ai-vN`` 冻结的权重数值（`ai-view` 票 15；spec §2.7 / §2.8 的 manifest）。

它是**生成文件**（整数字面量），冻结的是原始字节，不重新序列化——任何格式转换都会引入
"同一版本两种字节"的第二条真源。
"""

WEIGHTS_TABLE_NAMES = ("SHARED_B_ROWS", "IDENTITY_AC_ROWS")
"""``weights_data.py`` 必须定义的两张表（``sgs.agents.weights`` 的顶层 import 依赖同一契约）。"""

DEFAULT_VERSIONS_DIR = Path("runs") / "ai-versions"
"""gitignored 的副本目录（与 ``runs/`` 同一条 gitignore）。"""

MODULE_PREFIX = "_sgs_ai_version_"
"""独立模块名前缀：不覆盖 ``sgs.agents.heuristic``，两版可同进程并存。"""

WEIGHTS_MODULE = "sgs.agents.weights"
"""副本在 ``exec_module`` 期间看到的权重模块名（版本局部代理临时占位）。"""

_MISSING = object()
"""``sys.modules`` / 包属性"原本不存在"的哨兵（与"原本是 None"区分）。"""


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
    raw_player_class: type | None = None
    """副本**原样**的参与者类（未经兼容层包装）。

    包装只在选路表不全时发生（票 36）：``player_class is raw_player_class`` = 这个版本
    的选路表完整、兼容层没有插手。红-绿对照用它证明"红"是冻结副本自己的行为。
    """

    capability_gap_kinds: tuple[str, ...] = ()
    """这个版本的选路表缺的 ``QueryKind``（版本级、静态；见 `ai_eval.compat`）。"""

    weights_sha256: str | None = None
    """冻结的 ``weights_data.py`` 字节摘要；``None`` = 该 ref 里没有这份文件（08b 之前）。"""

    weights_path: Path | None = None
    """``runs/ai-versions/<safe>-weights.py`` 的副本路径；缺文件时 ``None``。"""

    weights_frozen: bool = False
    """``True`` = 权重按 ref 的字节冻结；``False`` = **显式空表**（不读主树）。"""

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


def _git_optional(ref: str, path: str, root: Path) -> bytes | None:
    """``git show <ref>:<path>``；**这个 ref 里没有该文件** ⇒ ``None``（不是错误）。

    "没有文件"与"ref 读不了"必须分开：前者是 08b 结构件之前的正常历史（显式空表），
    后者要响亮失败。判据用 ``git ls-tree`` 复核（不解析 git 的英文报错）。
    """
    completed = _run_git(root, "show", f"{ref}:{path}")
    if completed.returncode == 0:
        return completed.stdout
    present = _run_git(root, "ls-tree", "--name-only", ref, "--", path)
    if present.returncode == 0 and not present.stdout.strip():
        return None
    raise AiVersionError(
        f"读不到版本 {ref!r} 的 {path}（不静默回退 HEAD）：{_failure_detail(completed)}"
    )


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


def _freeze_copy(path: Path, data: bytes, *, ref: str, what: str) -> tuple[str, bytes]:
    """写副本 → 读回 → 逐字节比对；返回 ``(sha256(git 字节), 落盘字节)``。

    与 heuristic 副本同一纪律（spec §2.6）：写坏了 / 读不回就红，**点名 ref 与文件**；
    ``FileNotFoundError`` 不漏给调用方（缺文件也是"加载失败"，不是"没找到就当没有"）。
    """
    digest = hashlib.sha256(data).hexdigest()
    _write_copy(path, data)
    try:
        written = path.read_bytes()
    except OSError as error:
        raise AiVersionError(
            f"版本 {ref!r} 的 {what} 副本读不回（{path}）：{type(error).__name__}: {error}"
        ) from error
    if written != data:
        raise AiVersionError(
            f"版本 {ref!r} 的 {what} 副本与 git show 字节不一致（{path}）："
            f"sha256={hashlib.sha256(written).hexdigest()} != {digest}"
        )
    return digest, written


def _checked_rows(
    value: object, *, name: str, ref: str, path: Path
) -> tuple[tuple[str, int], ...]:
    """核行**容器形状**：必须是元组，每行是 ``(键, 整数)`` 二元组（值类型由 WeightTable 核）。"""
    if not isinstance(value, tuple):
        raise AiVersionError(
            f"版本 {ref!r} 的 {WEIGHTS_PATH} 里 {name} 必须是元组（{path}）："
            f"{type(value).__name__}"
        )
    for index, row in enumerate(value):
        if not isinstance(row, tuple) or len(row) != 2:
            raise AiVersionError(
                f"版本 {ref!r} 的 {WEIGHTS_PATH} 里 {name} 第 {index} 行不是"
                f" (键, 整数) 二元组（{path}）：{row!r}"
            )
    return value  # type: ignore[return-value] - 逐行形状已核；键 / 值语义交给 WeightTable


def _weights_rows(written: bytes, *, ref: str, path: Path) -> tuple[tuple[str, int], ...]:
    """从冻结的 ``weights_data.py`` 里读 **B 表**（AST 字面量，不执行副本）。

    "行格式坏"在这里 fail-closed，逐条点名 ref 与副本路径：不是合法 Python / 两张表缺失 /
    不是整数字面量元组 / ``WeightTable`` 的族与重复键检查失败。**只返回 B 表**——A/C 的
    拟合块不在本票的冻结范围（票 15「只冻结决策路径」），但文件是同一份生成物，故一并核
    形状（`weights.py` 顶层 import 依赖同一契约，坏 A/C 行会让整个引擎起不来）。
    """
    try:
        tree = ast.parse(written, filename=str(path))
    except (SyntaxError, ValueError) as error:
        raise AiVersionError(
            f"版本 {ref!r} 的 {WEIGHTS_PATH} 不是合法 Python（{path}）：{error}"
        ) from error
    literals: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if name not in WEIGHTS_TABLE_NAMES:
            continue
        if value is None:  # 只写类型标注、没有值：生成文件不会这样，按格式坏处理
            raise AiVersionError(
                f"版本 {ref!r} 的 {WEIGHTS_PATH} 里 {name} 没有值（{path}）"
            )
        try:
            literals[name] = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError) as error:
            raise AiVersionError(
                f"版本 {ref!r} 的 {WEIGHTS_PATH} 里 {name} 不是整数字面量表（{path}）：{error}"
            ) from error
    missing = [name for name in WEIGHTS_TABLE_NAMES if name not in literals]
    if missing:
        raise AiVersionError(
            f"版本 {ref!r} 的 {WEIGHTS_PATH} 缺表 {missing}（{path}）："
            "生成文件必须同时定义 SHARED_B_ROWS / IDENTITY_AC_ROWS"
        )
    b_rows = _checked_rows(
        literals["SHARED_B_ROWS"], name="SHARED_B_ROWS", ref=ref, path=path
    )
    ac_rows = _checked_rows(
        literals["IDENTITY_AC_ROWS"], name="IDENTITY_AC_ROWS", ref=ref, path=path
    )
    try:
        weight_models.WeightTable(
            name=f"shared_b@{_safe_name(ref)}",
            families=(weight_models.FAMILY_B,),
            rows=b_rows,
        )
        weight_models.WeightTable(
            name=f"identity_ac@{_safe_name(ref)}",
            families=(weight_models.FAMILY_A, weight_models.FAMILY_C),
            rows=ac_rows,
        )
    except (TypeError, ValueError) as error:
        raise AiVersionError(
            f"版本 {ref!r} 的 {WEIGHTS_PATH} 行格式坏（{path}）：{error}"
        ) from error
    return b_rows


def _import_copy(
    path: Path,
    *,
    ref: str,
    digest: str,
    weights_policy: ModuleType,
) -> object:
    """以独立模块名从副本 import；``__file__`` 必须指回副本。

    **包名指回 ``sgs.agents``**：`ai-view` 票 07 起 `heuristic.py` 有同包相对导入
    （``from . import identity``）。副本是**单文件**冻结（只冻结 `heuristic.py`），
    按"其余一律解析到当前引擎"的既有口径，它的兄弟模块应解析到**当前**的
    `sgs.agents.identity`——不把包名补回去，`exec_module` 会报
    ``attempted relative import with no known parent package``。旧版本没有相对导入，
    这一步对它们是无操作；副本自己的 ``__file__`` 断言不受影响。

    **权重（票 15）**：``from . import weights`` 会先命中 ``sgs.agents`` 包的 ``weights``
    属性，只改 ``sys.modules`` 不够（实测会拿到主树模块）。故 ``exec_module`` 期间把
    **两者**都临时指向 ``weights_policy``，``finally`` 里逐项还原——主树模块的字段一个
    不动，别的版本 / 别的线程拿到的仍是原模块。
    """
    # 限定在 `sgs.agents.` 下：`__spec__.parent` 因此就是 `sgs.agents`，相对导入解析到
    # **当前**的兄弟模块，且不会触发 `__package__ != __spec__.parent` 的弃用告警。
    name = f"sgs.agents.{MODULE_PREFIX}{_safe_name(ref)}_{digest[:8]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AiVersionError(f"版本 {ref!r} 的副本无法 import：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    package = importlib.import_module("sgs.agents")
    previous_module = sys.modules.get(WEIGHTS_MODULE, _MISSING)
    previous_attr = getattr(package, "weights", _MISSING)
    sys.modules[WEIGHTS_MODULE] = weights_policy
    setattr(package, "weights", weights_policy)
    try:
        try:
            spec.loader.exec_module(module)
        except Exception as error:  # noqa: BLE001 - 旧版加载失败要点名 ref 与原因
            raise AiVersionError(
                f"版本 {ref!r} 加载失败（{path}）：{type(error).__name__}: {error}"
            ) from error
    finally:
        if previous_module is _MISSING:
            sys.modules.pop(WEIGHTS_MODULE, None)
        else:
            sys.modules[WEIGHTS_MODULE] = previous_module
        if previous_attr is _MISSING:
            try:
                delattr(package, "weights")
            except AttributeError:  # 期间已有人删掉：还原目标就是"不存在"
                pass
        else:
            setattr(package, "weights", previous_attr)
    module_file = getattr(module, "__file__", None)
    if module_file is None or Path(module_file).resolve() != path.resolve():
        raise AiVersionError(
            f"版本 {ref!r} 的 __file__ 没有指向 runs 副本：{module_file!r} != {str(path)!r}"
        )
    bound = module.__dict__.get("weight_policy")
    if bound is not None and bound is not weights_policy:
        raise AiVersionError(
            f"版本 {ref!r} 的启发式绑定到了主树的 {WEIGHTS_MODULE}，不是版本局部权重（{path}）"
        )
    return module


def load_version(
    ref: str,
    *,
    repo_root: Path | None = None,
    versions_dir: Path | None = None,
) -> LoadedVersion:
    """加载 ref 指向的启发式模块与权重（缺 tag / 字节不一致 / 格式坏都响亮失败）。"""
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

    safe = _safe_name(ref)
    data = _git_show(ref, root)
    commit = git_commit(ref, repo_root=root)
    path = target_dir / f"{safe}.py"
    digest, _ = _freeze_copy(path, data, ref=ref, what=HEURISTIC_PATH)

    weights_bytes = _git_optional(ref, WEIGHTS_PATH, root)
    weights_rows: tuple[tuple[str, int], ...] = ()
    weights_sha256: str | None = None
    weights_path: Path | None = None
    if weights_bytes is None:
        # 08b 的结构件之前：**显式空表**（B 表 0 行 ⇒ 打分逐 float 等于手写锚），
        # 不读主树的拟合值、也不报错。weights_frozen=False 把这条回落记在台账面。
        weights_frozen = False
    else:
        weights_path = target_dir / f"{safe}-weights.py"
        weights_sha256, weights_written = _freeze_copy(
            weights_path, weights_bytes, ref=ref, what=WEIGHTS_PATH
        )
        weights_rows = _weights_rows(weights_written, ref=ref, path=weights_path)
        weights_frozen = True
    try:
        weights_policy = weight_models.local_policy(weights_rows, label=safe)
    except (TypeError, ValueError) as error:  # 防御：行格式已在 _weights_rows 核过
        raise AiVersionError(
            f"版本 {ref!r} 的权重表构造失败（{weights_path or path}）：{error}"
        ) from error

    module = _import_copy(path, ref=ref, digest=digest, weights_policy=weights_policy)
    player_class = getattr(module, "HeuristicPlayer", None)
    if player_class is None:
        raise AiVersionError(f"版本 {ref!r} 的 {HEURISTIC_PATH} 没有导出 HeuristicPlayer")
    gap_kinds = capability_gap_kinds(module)
    loaded = LoadedVersion(
        ref=ref,
        commit=commit,
        heuristic_sha256=digest,
        path=path,
        player_class=fail_closed_player_class(player_class, gap_kinds, ref=ref),
        raw_player_class=player_class,
        capability_gap_kinds=gap_kinds,
        weights_sha256=weights_sha256,
        weights_path=weights_path,
        weights_frozen=weights_frozen,
    )
    _CACHE[key] = loaded
    return loaded
