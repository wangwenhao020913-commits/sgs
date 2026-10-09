"""源码纪律扫描：用静态判据禁掉不确定来源（票 08 §7 / 票 26）。

落点 = **源码树本身**（静态扫描），不跑对局；跨进程的实测判决见 ``tests/test_determinism.py``。

四条机械判据：

1. ``random`` / ``secrets`` 全项目只出现在 ``engine/rng.py``（唯一随机源）；
2. 全域不 import ``time`` / ``datetime`` / ``uuid``（易变来源）与线程 / 异步 / 并发原语
   （引擎单线程）；
3. 不调用 ``id()`` / 裸 ``hash()``（对象地址、进程内哈希），也不 ``for`` 迭代
   ``set`` / ``frozenset``（集合迭代序逐进程变化）；
4. ``state`` 子包的 **pydantic 模型字段**里没有 ``set`` / ``frozenset``
   （``dict`` 自 3.7 起插入序有语言保证，故不在禁止之列）。

跨 ``PYTHONHASHSEED`` 的**实测**判决在 ``tests/test_determinism.py``；本文件是它的静态主力闸：
两者一静一动，都不可被"恰好没出问题"替代。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel

import sgs.state as state

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "sgs"
RNG_MODULE = SOURCE_ROOT / "engine" / "rng.py"

ONLY_IN_RNG = frozenset({"random", "secrets"})
"""随机库：只允许唯一随机源模块 import。"""

FORBIDDEN_MODULES = frozenset(
    {
        "time",
        "datetime",
        "uuid",
        "threading",
        "asyncio",
        "multiprocessing",
        "concurrent",
        "_thread",
    }
)
"""易变来源与并发原语：全域禁止（确定性 + 单线程，票 08 §7）。"""

UNORDERED_NAMES = frozenset({"set", "frozenset", "Set", "FrozenSet"})
"""无序容器名（含 ``typing`` 别名）；迭代它或把它当模型字段都违规。"""


def _source_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _trees() -> Iterator[tuple[Path, ast.Module]]:
    for path in _source_files():
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(tree: ast.Module) -> Iterator[tuple[str, int]]:
    """文件里出现的**顶层**模块名（``import a.b`` 与 ``from a.b import c`` 都算 ``a``）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入天然留在本子包内
                continue
            if node.module:
                yield node.module.split(".")[0], node.lineno


def _relative(path: Path) -> str:
    return str(path.relative_to(SOURCE_ROOT))


# ---------------------------------------------------------------------------
# 1. 唯一随机源
# ---------------------------------------------------------------------------


def test_random_and_secrets_appear_only_in_the_rng_module():
    offenders = [
        f"{_relative(path)}:{lineno} 导入了 {module}"
        for path, tree in _trees()
        for module, lineno in _imports(tree)
        if module in ONLY_IN_RNG and path != RNG_MODULE
    ]

    assert not offenders, "唯一随机源之外出现了随机库：" + "；".join(offenders)


def test_the_rng_module_really_is_the_one_that_imports_random_and_secrets():
    """防"扫描目标写错 ⇒ 空转也算通过"。"""
    imported = {module for module, _ in _imports(ast.parse(RNG_MODULE.read_text(encoding="utf-8")))}

    assert {"random", "secrets"} <= imported, "engine/rng.py 应当是全项目唯一 import 随机库的模块"


# ---------------------------------------------------------------------------
# 2. 易变来源与并发原语
# ---------------------------------------------------------------------------


def test_no_wall_clock_uuid_or_concurrency_imports():
    offenders = [
        f"{_relative(path)}:{lineno} 导入了 {module}"
        for path, tree in _trees()
        for module, lineno in _imports(tree)
        if module in FORBIDDEN_MODULES
    ]

    assert not offenders, "确定性/单线程纪律被破坏：" + "；".join(offenders)


# ---------------------------------------------------------------------------
# 3. 对象地址 / 进程内哈希 / 集合迭代序
# ---------------------------------------------------------------------------


def test_no_id_or_bare_hash_calls():
    offenders = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"id", "hash"}
            ):
                offenders.append(f"{_relative(path)}:{node.lineno} 调用了 {node.func.id}()")

    assert not offenders, "对象地址 / 进程内哈希不得作为键：" + "；".join(offenders)


def _is_unordered_iterable(node: ast.expr) -> bool:
    """``for`` / 推导式迭代的是不是无序容器（``set`` / ``frozenset`` 字面量、推导式或调用）。"""
    if isinstance(node, (ast.Set, ast.SetComp)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in UNORDERED_NAMES
    )


def test_no_iteration_over_unordered_collections():
    """成员判定允许（``x in {…}``），**迭代**不允许；``sorted(set)`` 因为排序而确定，也不在此列。"""
    offenders = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and _is_unordered_iterable(node.iter):
                offenders.append(f"{_relative(path)}:{node.lineno} for 迭代了无序容器")
            elif isinstance(node, ast.comprehension) and _is_unordered_iterable(node.iter):
                offenders.append(f"{_relative(path)}:{node.lineno} 推导式迭代了无序容器")

    assert not offenders, "集合迭代序逐进程变化：" + "；".join(offenders)


# ---------------------------------------------------------------------------
# 4. state 模型字段
# ---------------------------------------------------------------------------


def _pydantic_models(module: Any) -> list[type[BaseModel]]:
    return [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel
    ]


def _mentions_unordered(annotation: Any) -> bool:
    if get_origin(annotation) in (set, frozenset):
        return True
    return any(_mentions_unordered(argument) for argument in get_args(annotation))


def test_state_models_have_no_set_or_frozenset_fields():
    """只经 ``sgs.state`` 的公开面拿模型：子包纪律禁止外部 import 深层路径（票 02 §2）。"""
    checked = 0
    offenders = []
    for model in _pydantic_models(state):
        for name, field in model.model_fields.items():
            checked += 1
            if _mentions_unordered(field.annotation):
                offenders.append(f"{model.__module__}.{model.__qualname__}.{name}: {field.annotation}")

    assert checked >= 20, f"只查到 {checked} 个 state 模型字段；扫描目标可能指错了"
    assert not offenders, "state 模型字段不得是 set/frozenset：" + "；".join(offenders)


def test_the_scan_actually_covered_the_source_tree():
    files = _source_files()

    assert len(files) >= 10, f"只扫到 {len(files)} 个源文件；SOURCE_ROOT 可能指错了"
    assert RNG_MODULE.exists(), f"找不到唯一随机源模块：{RNG_MODULE}"


# ---------------------------------------------------------------------------
# 5. 技能逻辑不进主干（票 07 §5 / 票 30）
# ---------------------------------------------------------------------------

ENGINE_ROOT = SOURCE_ROOT / "engine"


def _engine_trees() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(ENGINE_ROOT.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_engine_contains_no_concrete_skill_id_or_equipment_name():
    """票 07 §5 的机械判据：``engine/`` 不出现任何装备名或 ``equip.`` 技能 id。

    ``Engine.emit_frame_timing`` 只调 ``skills.dispatch_timing``（通用派发）；装备效果
    与技能 id 一律住 ``skills/``。这条扫描是"禁止技能逻辑进主干"（`06` L65）的兜底。
    """
    from sgs.skills import EQUIPMENT_CARD_TYPE_IDS

    forbidden = {"equip."} | set(EQUIPMENT_CARD_TYPE_IDS)
    offenders = []
    for path, tree in _engine_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if "equip." in text or text in forbidden:
                    offenders.append(f"{path.name}:{node.lineno} 字符串 {text!r}")

    assert not offenders, "引擎侧出现具体技能 id / 装备名：" + "；".join(offenders)
    assert len(list(_engine_trees())) >= 4, "扫描目标可能指错了"


def test_engine_does_not_import_concrete_skill_modules():
    """票 07 §5：``engine/`` 只许从 ``sgs.skills`` **包入口**取通用派发，不得深入子模块。

    引擎侧只认 ``sgs.skills`` 这一个入口（``dispatch_timing`` / ``resolve_instances`` 等），
    具体技能目录 / 虚拟牌构造住子模块，引擎不得直接 import——否则"唯一入口"形同虚设。
    """
    offenders = []
    for path, tree in _engine_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("sgs.skills."):
                    offenders.append(f"{path.name}:{node.lineno} from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("sgs.skills."):
                        offenders.append(f"{path.name}:{node.lineno} import {alias.name}")

    assert not offenders, "引擎侧深入了 skills 子模块：" + "；".join(offenders)


def test_call_op_is_dispatched_through_the_op_registry():
    """``CallOp`` 一律查 :data:`sgs.effects.OPS` 注册表，``engine/`` 不出现具体效果名。"""
    source = (ENGINE_ROOT / "game.py").read_text(encoding="utf-8")

    assert "OPS.get(" in source and "OPS[" in source, (
        "apply_effects / apply_judge_after 必须按名查 OPS 注册表"
    )


def test_every_behavior_call_op_is_registered():
    from sgs.effects import CARD_BEHAVIORS, OPS, CallOp

    missing = [
        step.op
        for behavior in CARD_BEHAVIORS.values()
        for step in behavior.effects
        if isinstance(step, CallOp) and step.op not in OPS
    ]
    assert not missing, f"行为表引用了未登记的 op：{missing}"
