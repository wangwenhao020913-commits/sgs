"""剧本库的加载接缝、内置空壳与内容指纹（M7 票 14；spec §2 / §7，票 03 §1、§6、§7）。

三段门的**逐条判据**落在 ``tests/data/test_script_library_validation.py``；本文件只钉
"数据面能经 ``sgs.data`` 公开接缝进来"这一层：

- 内置空壳 ``scripts.huangjin.json`` 走**同一份** ``validate_script_library`` 加载通过
  （空壳是 schema 合法的，真实内容归票 28）；
- ``script_digest`` 是**规范化 dump 的 sha256**：键序 / 空白不同 ⇒ 同一指纹，内容变了 ⇒ 变；
- 新增的公开名进 ``sgs.data.__all__``（深导扫描由 ``tests/test_scaffold.py`` 兜）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sgs.data import (
    SCRIPT_SCHEMA_VERSION,
    ScriptLibrary,
    library_digest,
    load_builtin_script_library,
    load_script_library,
    script_digest,
    validate_script_library,
)

BUILTIN_FILE = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "sgs"
    / "data"
    / "files"
    / "scripts.huangjin.json"
)
"""内置剧本库的仓内路径（单层 glob 随包：``pyproject.toml:27`` 不用改）。"""

DIGEST_SHAPE = re.compile(r"sha256:[0-9a-f]{64}")
"""``script_digest`` 的形状：``sha256:`` + 64 位小写十六进制。"""


def _reversed_keys(value: Any) -> Any:
    """递归把每个对象的键序**倒过来**（JSON / dict 的键序不该影响指纹）。"""
    if isinstance(value, dict):
        return {key: _reversed_keys(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reversed_keys(item) for item in value]
    return value


def test_the_builtin_shell_loads_through_the_same_validator() -> None:
    """内置空壳：1 剧本 / 2 大节 / 网格 + 节点边；过整份校验器，清单为空。"""
    library = load_builtin_script_library()

    assert isinstance(library, ScriptLibrary)
    assert library.script_schema_version == SCRIPT_SCHEMA_VERSION
    assert [script.id for script in library.scripts] == ["huangjin"]
    script = library.scripts[0]
    assert len(script.layers) == 2, "票 14 只落空壳：1 剧本 / 2 大节"
    assert all(layer.nodes and layer.edges for layer in script.layers), (
        "每个大节都要有网格与少量节点边（真实内容归票 28）"
    )
    # 同一份文件走同一个校验函数：空壳是 schema 合法的。
    assert validate_script_library(BUILTIN_FILE.read_text(encoding="utf-8"),
                                   file="files/scripts.huangjin.json") == ()
    # 两条加载路（打包文件 / 给定 JSON）得到同一份库。
    assert library.model_dump(by_alias=True) == load_script_library(
        BUILTIN_FILE.read_text(encoding="utf-8"), file="files/scripts.huangjin.json"
    ).model_dump(by_alias=True)


def test_the_new_loader_seam_is_exported_from_the_data_package() -> None:
    """``sgs.data.__all__`` 更新：新接缝只从这里拿，外部不 import 深层模块。"""
    import sgs.data as data

    for name in (
        "KNOWN_SCRIPT_SCHEMA_VERSIONS",
        "SCRIPT_LIBRARY_FILENAME",
        "SCRIPT_SCHEMA_VERSION",
        "Script",
        "ScriptLibrary",
        "validate_script_library",
        "load_script_library",
        "load_builtin_script_library",
        "script_digest",
        "library_digest",
        "canonical_json",
    ):
        assert name in data.__all__, f"sgs.data.__all__ 少了 {name}"
        assert hasattr(data, name), f"sgs.data 没有导出 {name}"


def test_the_digest_is_the_sha256_of_a_canonical_dump() -> None:
    """规范化 dump 的 sha256：键序 / 缩进不改变指纹，内容一变就变。"""
    library = load_builtin_script_library()
    script = library.scripts[0]
    digest = script_digest(script)

    assert DIGEST_SHAPE.fullmatch(digest), f"指纹形状：{digest!r}"

    # 同一份内容的另一种写法：键序全倒过来 + 重新序列化 ⇒ 同一个指纹。
    reordered = _reversed_keys(json.loads(json.dumps(library.model_dump(by_alias=True))))
    reloaded = load_script_library(reordered, file="reordered.json")
    assert script_digest(reloaded.scripts[0]) == digest
    assert library_digest(reloaded) == library_digest(library)

    # 内容不同 ⇒ 指纹不同（不是空转：版本号 +1 也算语义变更）。
    bumped = script.model_copy(update={"version": script.version + 1})
    assert script_digest(bumped) != digest
    assert script_digest(library.scripts[0]) == digest, "指纹计算不改原对象"


def test_the_library_digest_covers_the_shared_tables_too() -> None:
    """库指纹涵盖共享表：改一个房间 ⇒ 库指纹变，但那一份剧本的指纹不变。"""
    library = load_builtin_script_library()
    rooms = list(library.rooms)
    rooms[0] = rooms[0].model_copy(update={"name": rooms[0].name + "改"})
    changed = library.model_copy(update={"rooms": tuple(rooms)})

    assert library_digest(changed) != library_digest(library)
    assert script_digest(changed.scripts[0]) == script_digest(library.scripts[0])


def test_the_script_version_is_audit_only_and_never_a_gate() -> None:
    """``script.version`` / ``script_digest`` 只做审计：loader 里没有拿它们拒绝的路径。"""
    library = load_builtin_script_library()
    bumped = library.scripts[0].model_copy(update={"version": 99})
    raw = library.model_copy(update={"scripts": (bumped,)}).model_dump(by_alias=True)

    assert validate_script_library(raw) == (), "语义版本 +1 不拒（票 03 §7）"
    assert script_digest(bumped) != script_digest(library.scripts[0]), (
        "版本是内容的一部分：它变 ⇒ 指纹变（审计用途）"
    )
