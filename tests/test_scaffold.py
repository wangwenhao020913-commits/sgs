"""脚手架：包可导入、八个子包齐备、子包边界纪律成立。"""

from __future__ import annotations

import ast
import importlib
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

SUBPACKAGES = (
    "data",
    "state",
    "timing",
    "resolution",
    "effects",
    "skills",
    "engine",
    "agents",
)

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "sgs"


def test_import_sgs():
    import sgs

    assert sgs.__name__ == "sgs"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_importable(name):
    module = importlib.import_module(f"sgs.{name}")

    assert module.__name__ == f"sgs.{name}"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_declares_its_public_interface(name):
    """子包只再导出公开接口，不把子模块本身当接口暴露出去。"""
    module = importlib.import_module(f"sgs.{name}")

    assert isinstance(module.__all__, tuple), f"sgs.{name} 未声明 __all__"
    for public in module.__all__:
        assert isinstance(public, str), f"sgs.{name}.__all__ 只能是名字"
        assert not isinstance(getattr(module, public), types.ModuleType), (
            f"sgs.{name}.__all__ 暴露了子模块 {public}；子包只应再导出公开接口"
        )


def test_deep_imports_stay_inside_their_own_subpackage():
    """本仓库源码里不存在跨子包的深层导入。

    纪律是"子包之外只允许导入 ``sgs`` 或 ``sgs.<子包>``"。Python 无法真正阻止
    外部写 ``import sgs.data.models``，所以这条纪律由本扫描（加上评审）执行，
    而不是靠语言机制；扫描范围限定在本仓库源码内。
    """
    violations: list[str] = []
    scanned = 0
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        scanned += 1
        owner = _owner_subpackage(path)
        for module, lineno in _imported_sgs_modules(path):
            parts = module.split(".")
            if len(parts) < 3:
                continue
            if parts[1] == owner:
                continue
            violations.append(f"{path.relative_to(SOURCE_ROOT)}:{lineno} 导入了 {module}")

    assert not violations, "跨子包深层导入：" + "；".join(violations)
    # 防"扫描目标写错 ⇒ 空转也算通过"：源码树不可能只剩几个文件。
    assert scanned >= 10, f"只扫到 {scanned} 个源文件；SOURCE_ROOT 可能指错了"


def _owner_subpackage(path: Path) -> str | None:
    """文件所属的子包名；顶层模块（如 ``cli.py``）没有归属。"""
    parts = path.relative_to(SOURCE_ROOT).parts
    if len(parts) >= 2 and parts[0] in SUBPACKAGES:
        return parts[0]
    return None


def test_the_leaf_primitives_never_reach_for_the_resolution_stack():
    """叶子原语（票 07 §1 / 票 20）：只改状态，绝不压栈 / 发询问 / 暂停 / 访问结算栈。

    结构保证 = ``effects.primitives`` 连 ``resolution`` / ``engine`` 都不导入，
    因而拿不到栈、也发不出询问；只有 ``state`` 句柄与返回 delta 两条路。
    """
    path = SOURCE_ROOT / "effects" / "primitives.py"
    imported = {
        node.module
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "sgs.resolution" not in imported, "叶子原语不得导入结算栈"
    assert "sgs.engine" not in imported, "叶子原语不得导入引擎"
    assert "sgs.state" in imported, "叶子原语只经 state 句柄改状态"


def _imported_sgs_modules(path: Path) -> Iterator[tuple[str, int]]:
    """文件里出现的 ``sgs.*`` 绝对导入模块名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "sgs":
                    yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入天然留在同一子包内
                continue
            if node.module and node.module.split(".")[0] == "sgs":
                yield node.module, node.lineno
