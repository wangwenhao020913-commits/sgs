"""武将立绘的目录索引（hero-portraits 票 01）。

立绘是**厂商版权素材**：落点是 ``pic/portraits/``，**随仓分发**（版权与移除口径见仓库根
``NOTICE`` 第 2 节）；``SGS_ART_DIR`` 可把根换到别处（冻结包里由启动器显式指向包旁的
``pic/portraits``）。目录形状与生成工具（``tools/build-portraits.py``）同源::

    pic/portraits/
    ├── index.json                    ← 生成期快照（人看用；服务端不读它；不入库）
    ├── 关羽/关羽/关羽.jpg             ← 有图：<真名>/<显示名称>/<显示名称>.<扩展名>
    └── 关羽/界关羽/                  ← 没图：**空目录**（立绘空位的槽；不入库）

**画像键**（``GLOSSARY.md`` §界面与版式）：显示名称就是目录名——生成工具已按 A+B+C 规则
（精确 → 去版字尾 → 去「·」前缀）把资源包解析成这个名字，服务端不再做第二遍回退。

**每次调用现扫**：240 个目录是毫秒级的；用户手编 ``作者.txt`` 后刷新页面即生效，不需要
重跑生成工具，也不需要重启服务。

作者是同目录的 ``作者.txt``（大小写不敏感）。用户会在 Windows 记事本里编辑它，故字节
可能是 UTF-8（带 / 不带 BOM）或 ANSI（GBK）：解码按 ``utf-8-sig`` → ``utf-8`` → ``gbk``
逐级回退。**strip 后为空 ⇒ ``""``**（前端据此一个节点都不渲染）；非空则**原样**返回
（含换行与行尾空白），前端零正则、零拼接。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from sgs_web import REPO_ROOT

__all__ = [
    "AUTHOR_FILENAME",
    "ART_DIR_ENV",
    "PORTRAITS_ROOT",
    "image_mime",
    "portrait_index",
    "portraits_root",
    "resolve_portrait",
]

PORTRAITS_ROOT: Final[Path] = REPO_ROOT / "pic" / "portraits"
"""立绘根目录（缺省 = ``<repo>/pic/portraits``）；``SGS_ART_DIR`` 可直接覆盖它。"""

ART_DIR_ENV: Final = "SGS_ART_DIR"
"""覆盖立绘根目录的环境变量名（测试 / 换机器换素材盘用）。"""

AUTHOR_FILENAME: Final = "作者.txt"
"""每个武将目录里的作者文件名（用户手编；大小写不敏感）。"""

_PORTRAIT_SUFFIXES: Final = (".jpg", ".jpeg", ".png")
"""生成工具落盘的扩展名（按**魔数**定：资源包里 5 个 ``.jpg`` 实为 PNG 容器）。"""


def portraits_root() -> Path:
    """当前生效的立绘根目录：``SGS_ART_DIR`` 优先，否则 :data:`PORTRAITS_ROOT`。

    环境变量在**每次调用**时读（不是 import 时），故测试可以直接
    ``monkeypatch.setenv``，也允许同一个进程换素材根。
    """
    override = os.environ.get(ART_DIR_ENV, "").strip()
    return Path(override) if override else PORTRAITS_ROOT


def image_mime(data: bytes) -> str:
    """按**魔数**判图片 MIME（不看扩展名——资源包里有 5 个 ``.jpg`` 实为 PNG 容器）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "application/octet-stream"


def resolve_portrait(relative: str) -> Path | None:
    """把 ``/art/`` 后的相对路径解析成立绘根里的**已存在文件**；越界 / 缺失 ⇒ ``None``。

    越界判据与 ``serve.static`` 同一姿态（``resolve()`` 后必须在根的 ``parents`` 里）：
    ``%2e%2e%2f`` 这类编码过的穿越在 ``unquote`` 之后由这里统一拒掉。
    """
    root = portraits_root().resolve()
    target = (root / relative).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


def _author_bytes(directory: Path) -> bytes | None:
    """读目录里的作者文件（大小写不敏感）；没有 ⇒ ``None``。"""
    wanted = AUTHOR_FILENAME.lower()
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.name.lower() == wanted and entry.is_file():
            try:
                return entry.read_bytes()
            except OSError:
                return None
    return None


def _decode_author(raw: bytes) -> str:
    """``utf-8-sig`` → ``utf-8`` → ``gbk`` 逐级回退；strip 后为空 ⇒ ``""``。"""
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - GBK 覆盖了几乎所有字节序列，走到这里只会是坏档
        text = raw.decode("utf-8", errors="replace")
    return text if text.strip() else ""


def _portrait_of(hero_dir: Path, root: Path) -> str | None:
    """``<显示名称>.<jpg|jpeg|png>`` → 相对立绘根的 POSIX 路径；没有图 ⇒ ``None``。"""
    for suffix in _PORTRAIT_SUFFIXES:
        candidate = hero_dir / f"{hero_dir.name}{suffix}"
        if candidate.is_file():
            return candidate.relative_to(root).as_posix()
    return None


def portrait_index() -> dict[str, dict[str, object]]:
    """扫一遍立绘根，返回 ``{显示名称: {"portrait": 相对路径 | None, "author": str}}``。

    **不缓存、不落盘**：目录树变了（手编作者、替换图片、整个素材盘换了）下一次调用就看得见。
    立绘根不存在（出厂无立绘 / ``SGS_ART_DIR`` 指空）⇒ ``{}``，**不抛**。
    """
    root = portraits_root()
    try:
        true_dirs = sorted(entry for entry in root.iterdir() if entry.is_dir())
    except OSError:
        return {}
    index: dict[str, dict[str, object]] = {}
    for true_dir in true_dirs:
        try:
            hero_dirs = sorted(entry for entry in true_dir.iterdir() if entry.is_dir())
        except OSError:
            continue
        for hero_dir in hero_dirs:
            raw_author = _author_bytes(hero_dir)
            index[hero_dir.name] = {
                "portrait": _portrait_of(hero_dir, root),
                "author": "" if raw_author is None else _decode_author(raw_author),
            }
    return index
