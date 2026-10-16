"""票 22：**询问标识**的机械闸 —— 同一帧里没有两个询问共用缓存槽。

背景：``Engine.ask_selection`` 的帧内缓存（答案 + 自建的面）按 ``(owner, key)`` 分桶，即
``GLOSSARY.md`` 的**询问标识**：``owner`` 是**询问位置**（逐处唯一），``key`` 是**询问名**
（效果读答案、前端取文案）。票 21 的 ``KeyError`` 就是两者混同：两个步骤共用 ``key`` ⇒
后者命中前者的答案，取回的 id 与**自己那份面**不同源。

本文件是那条不变量的**机械发现**，不手抄名单：

1. **流水线侧**：从**已导入的全部 ``sgs.*`` 模块**里机械发现模块级 :class:`SkillFlow`
   （不手抄 ``ACTIVE_SKILLS`` / ``CATALOG`` 的引用点，也不漏掉别处引用的流水线），断言
   ``name`` 非空且两两不同，并断言**每条模块级流水线都被登记**（``ACTIVE_SKILLS`` 的
   ``flow`` 或某个 ``SkillDef`` 的 ``Hook.flow`` / ``Seam.flow``）。位置 = ``(name, index)``
   （``run_flow`` 的 ``enumerate``），序号天然两两不同，故"名字两两不同"**等价于**
   "(owner, key) 两两不同"。**不另写一条把名字+序号重新拼一遍再断唯一的断言** —— 那种
   断言恒真、属 `CODING_STANDARDS.md` §4 的"永远绿"（本票首版真这么写过，评审两轴都抓出）。
2. **手写侧**：``ast`` 扫描 ``src/sgs/**`` 里全部 ``ask_selection(...)`` 调用点，断言每个都
   显式给了 ``owner=``、且它的取值能**静态解析**到一个模块级 ``_ASK_*`` 字符串常量
   （唯一的转发约定：``ask_owner=`` 形参，见 :func:`sgs.effects.ops._take_one_zone_card`；
   转发按**全仓**调用点解析，跨模块调用不带 ``ask_owner=`` 也会判红）。解析出的常量集合
   必须与源码里声明的 ``_ASK_*`` 常量集合**逐项相等** —— 新增调用点不带常量、或新加常量
   没有调用点，都会在这里变红；常量值两两不同则挡住"两处复用同一个常量"。

**范围裁定**：本票的机制是 ``ask_selection`` 的帧内缓存，故手写侧只走 ``ask_selection``。
手写的 ``ask_option`` 点（【雌雄双股剑】选项、【离间】选人、【遗计】交牌）不在此闸内 ——
它们的分桶键是 ``(skill_id, actor)``（见 :meth:`Engine.ask_option`），不是 ``owner``，
撞车面与修法都不同，不属本票范围。

源码只在**测试执行时**解析（``_sources`` 带缓存、不在 import 期跑），故收集测试本身不会
因为某个源文件正被编辑而炸。
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import sgs.skills  # noqa: F401  （触发各技能模块导入，好让 sys.modules 里齐全）
from sgs.skills.active import ACTIVE_SKILLS
from sgs.skills.catalog import CATALOG
from sgs.skills.flow import AskIdentity, SkillFlow

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "sgs"
FLOW_MODULE = SRC_ROOT / "skills" / "flow.py"

ASK_PREFIX = "_ASK_"
FORWARDED_OWNER_PARAM = "ask_owner"
"""转发 ``owner=`` 的形参名约定（目前唯一使用者：``ops._take_one_zone_card``）。"""


# ---------------------------------------------------------------------------
# ① 流水线侧：全部模块级 SkillFlow
# ---------------------------------------------------------------------------


def _module_flows() -> dict[int, tuple[str, SkillFlow]]:
    """已导入 ``sgs.*`` 模块里**全部**模块级 :class:`SkillFlow`：``id(flow) -> (出处, flow)``。

    机械发现（不手抄名单）：一条流水线只要定义在某个 ``sgs.*`` 模块的顶层就会被走到，
    无论它是被主动技、钩子还是接缝引用。
    """
    found: dict[int, tuple[str, SkillFlow]] = {}
    for module_name, module in sorted(sys.modules.items()):
        if not module_name.startswith("sgs."):
            continue
        for attribute, value in vars(module).items():
            if isinstance(value, SkillFlow):
                found.setdefault(id(value), (f"{module_name}.{attribute}", value))
    return found


def _registered_flows() -> dict[int, str]:
    """登记处引用的流水线：``id(flow) -> 登记处``（主动技 + 钩子 + 接缝）。"""
    found: dict[int, str] = {}
    for skill in ACTIVE_SKILLS:
        if skill.flow is not None:
            found.setdefault(id(skill.flow), f"ACTIVE_SKILLS[{skill.skill_id}]")
    for skill_id, definition in CATALOG.items():
        for hook in definition.hooks:
            if hook.flow is not None:
                found.setdefault(id(hook.flow), f"{skill_id}:{hook.timing.value}")
        for seam in definition.seams:
            if seam.flow is not None:
                found.setdefault(id(seam.flow), f"{skill_id}:{seam.kind.value}")
    return found


def test_every_flow_is_defined_registered_and_uniquely_named():
    """全部流水线：模块级、被登记、``name`` 非空且两两不同。

    位置 = ``(name, index)``，序号由 ``run_flow`` 的 ``enumerate`` 给出 ⇒ 名字唯一即
    "(owner, key) 两两不同"。故这里不重复断言那个恒真的推论（见模块 docstring）。
    """
    module_flows = _module_flows()
    registered = _registered_flows()
    assert module_flows, "机械发现必须找到模块级流水线（找不到说明枚举口径坏了）"

    unregistered = {
        where: flow.name
        for id_, (where, flow) in module_flows.items()
        if id_ not in registered
    }
    assert not unregistered, (
        f"模块级流水线没有被任何登记处引用（定义即死代码？）：{unregistered}"
    )
    dangling = {where for id_, where in registered.items() if id_ not in module_flows}
    assert not dangling, f"登记处引用了非模块级流水线：{dangling}"

    names: dict[str, list[str]] = {}
    for where, flow in module_flows.values():
        assert flow.name, f"{where} 的流水线缺 name"
        names.setdefault(flow.name, []).append(where)
    duplicated = {name: where for name, where in names.items() if len(where) > 1}
    assert not duplicated, f"同一 name 被多条流水线共用：{duplicated}"


# ---------------------------------------------------------------------------
# ② 手写侧：ast 机械发现全部 ask_selection 调用点
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Source:
    """一个已解析的源文件 + 每个调用点的宿主函数名（供 ``ask_owner`` 转发解析）。"""

    path: Path
    tree: ast.Module
    enclosing: dict[int, str]


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """``id(节点) -> 最近的函数名``：给每个调用点认领它的宿主函数。"""
    owner: dict[int, str] = {}

    def walk(node: ast.AST, current: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, ast.FunctionDef) else None
            if isinstance(child, ast.Call):
                owner[id(child)] = current or "<module>"
            walk(child, name or current)

    walk(tree, None)
    return owner


@lru_cache(maxsize=1)
def _sources() -> tuple[_Source, ...]:
    """``src/sgs/**/*.py`` 的解析结果（**测试执行时**才算，不在 import 期跑）。"""
    out: list[_Source] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        out.append(_Source(path=path, tree=tree, enclosing=_enclosing_functions(tree)))
    return tuple(out)


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _is_ask_selection(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "ask_selection"


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _module_constant_names(tree: ast.Module) -> set[str]:
    """模块顶层赋值出来的名字（含 ``_ASK_*`` 与转发用的其它常量）。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _call_sites(name: str) -> list[tuple[_Source, ast.Call]]:
    """**全仓**对 ``name`` 的调用点（含跨模块的 ``module.name(...)``）。

    转发解析必须跨文件：只查转发 helper 所在文件的话，别处一条不带 ``ask_owner=`` 的
    调用会静默漏过（评审 Standards 轴指出的洞）。
    """
    found: list[tuple[_Source, ast.Call]] = []
    for source in _sources():
        for node in ast.walk(source.tree):
            if isinstance(node, ast.Call) and _called_name(node) == name:
                found.append((source, node))
    return found


def _resolve_owner(
    source: _Source,
    enclosing: str,
    expr: ast.expr,
    *,
    allow_forwarding: bool = True,
) -> list[tuple[str, str]]:
    """把 ``owner=`` 的取值解析成模块级常量 ``(模块路径文本, 常量名)``。

    只认两档：模块级常量名（``_ASK_*``）与**显式转发形参** ``ask_owner``（后者的每个调用点
    必须再解析成模块级常量）。其余形态一律判红 —— 这正是"新调用点必须自带位置"的机械表达。
    """
    if isinstance(expr, ast.Name):
        if expr.id in _module_constant_names(source.tree):
            return [(_rel(source.path), expr.id)]
        if allow_forwarding and expr.id == FORWARDED_OWNER_PARAM:
            resolved: list[tuple[str, str]] = []
            for caller, call in _call_sites(enclosing):
                argument = _keyword(call, FORWARDED_OWNER_PARAM)
                assert argument is not None, (
                    f"{_rel(caller.path)}:{call.lineno} 调用 {enclosing} 时必须显式给 "
                    f"{FORWARDED_OWNER_PARAM}= 一个模块级常量"
                )
                resolved.extend(
                    _resolve_owner(
                        caller,
                        caller.enclosing.get(id(call), "<module>"),
                        argument,
                        allow_forwarding=False,
                    )
                )
            assert resolved, (
                f"{_rel(source.path)}: {enclosing} 的 {FORWARDED_OWNER_PARAM} 没有常量调用点"
            )
            return resolved
    if isinstance(expr, ast.Attribute):
        return [(_rel(source.path), expr.attr)]
    raise AssertionError(
        f"{_rel(source.path)}:{getattr(expr, 'lineno', '?')} 的 owner= 不是模块级 _ASK_* 常量："
        f"{ast.dump(expr)}"
    )


def _discovered_ask_owners() -> set[tuple[str, str]]:
    """AST 扫出的全部手写询问位置常量。"""
    discovered: set[tuple[str, str]] = set()
    for source in _sources():
        if source.path == FLOW_MODULE:
            # 步骤执行器的位置是 (flow.name, index)，由 ① 机械枚举覆盖。
            continue
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Call) or not _is_ask_selection(node):
                continue
            owner = _keyword(node, "owner")
            assert owner is not None, (
                f"{_rel(source.path)}:{node.lineno} 的 ask_selection 没给 owner= "
                "（票 22：询问位置必填）"
            )
            discovered.update(_resolve_owner(source, source.enclosing[id(node)], owner))
    assert discovered, "机械发现必须找到手写询问位置（找不到说明扫描口径坏了）"
    return discovered


def _declared_ask_owners() -> dict[tuple[str, str], AskIdentity]:
    """源码里声明的 ``_ASK_*`` 常量：``(模块路径文本, 常量名) -> 值``。

    从 AST 取（而不是硬编码模块名单 + ``importlib``）：新加一个声明 ``_ASK_*`` 的模块
    不必回来改本文件；值必须是**字符串字面量**，否则静态解析不成"位置"。
    """
    declared: dict[tuple[str, str], AskIdentity] = {}
    for source in _sources():
        for node in source.tree.body:
            name: str | None = None
            value_node: ast.expr | None = None
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                name = node.targets[0].id
                value_node = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
                value_node = node.value
            if name is None or not name.startswith(ASK_PREFIX):
                continue
            assert (
                isinstance(value_node, ast.Constant)
                and isinstance(value_node.value, str)
                and value_node.value
            ), f"{_rel(source.path)}:{node.lineno} 的 {name} 必须是非空字符串字面量"
            declared[(_rel(source.path), name)] = value_node.value
    assert declared, "源码里没有任何 _ASK_* 常量（扫描口径坏了）"
    return declared


def test_every_ask_selection_call_site_declares_a_resolvable_owner():
    """AST 发现 == 源码声明（双向覆盖）：既挡漏给位置的调用点，也挡没人用的常量。"""
    discovered = _discovered_ask_owners()
    declared = _declared_ask_owners()
    assert discovered == set(declared), (
        "手写询问位置常量集合与 AST 发现不一致：\n"
        f"  只有调用点：{sorted(discovered - set(declared))}\n"
        f"  只有声明  ：{sorted(set(declared) - discovered)}"
    )


def test_handwritten_ask_owner_values_are_pairwise_distinct():
    """位置值两两不同：复用同一个常量 = 两个询问共用缓存槽，正是票 22 要挡的。"""
    declared = _declared_ask_owners()
    by_value: dict[str, list[tuple[str, str]]] = {}
    for key, value in declared.items():
        by_value.setdefault(str(value), []).append(key)
    duplicated = {value: keys for value, keys in by_value.items() if len(keys) > 1}
    assert not duplicated, f"同一个询问位置值被多处复用：{duplicated}"
