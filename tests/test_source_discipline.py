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

此外 §5 是"技能逻辑不进主干"；§6 是本文件里的**另一条纪律**——**档位纪律**：
它扫 ``tests/`` 而非源码树，是 ``GLOSSARY.md`` 里与**源码纪律扫描**并列的独立术语（retro 票 03）。

跨 ``PYTHONHASHSEED`` 的**实测**判决在 ``tests/test_determinism.py``；本文件是它的静态主力闸：
两者一静一动，都不可被"恰好没出问题"替代。
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, get_args, get_origin

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

SKILL_ID_PREFIXES = ("equip.", "hero.")
"""具体技能 id 的前缀：装备（票 07 §5）与武将（票 01）。落进 ``engine/`` 即判红。

武将技能进 ``skills/`` 之后，这条扫描若只认 ``equip.``，票 02–12 就能把
``hero.<武将>.<技能>`` 漏进主干而不判红——票 01 的验收正是要它**非空转**。
"""


def _skill_literal_offender(text: str, forbidden_names: frozenset[str]) -> bool:
    """这段字符串字面量是不是"具体技能 id / 装备名"（抽出来是为了能对它单独跑合成反例）。"""
    return any(prefix in text for prefix in SKILL_ID_PREFIXES) or text in forbidden_names


def _engine_trees() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(ENGINE_ROOT.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_engine_contains_no_concrete_skill_id_or_equipment_name():
    """票 07 §5 / 票 01 的机械判据：``engine/`` 不出现装备名、``equip.`` 或 ``hero.`` 技能 id。

    ``Engine.emit_frame_timing`` 只调 ``skills.dispatch_timing``（通用派发）；装备效果、
    武将技能与具体技能 id 一律住 ``skills/``。这条扫描是"禁止技能逻辑进主干"（`06` L65）
    的兜底，覆盖两类载体。
    """
    from sgs.skills import EQUIPMENT_CARD_TYPE_IDS

    forbidden_names = frozenset(EQUIPMENT_CARD_TYPE_IDS)
    offenders = []
    for path, tree in _engine_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if _skill_literal_offender(text, forbidden_names):
                    offenders.append(f"{path.name}:{node.lineno} 字符串 {text!r}")

    assert not offenders, "引擎侧出现具体技能 id / 装备名：" + "；".join(offenders)
    assert len(list(_engine_trees())) >= 4, "扫描目标可能指错了"


def test_the_skill_id_scan_goes_red_on_a_hero_literal_too():
    """合成反例：``hero.`` 前缀必须与 ``equip.`` 同等判红（票 01 验收 5 不能空转）。"""
    names = frozenset({"crossbow"})

    assert _skill_literal_offender("equip.crossbow", names)
    assert _skill_literal_offender("hero.caocao.hujia", names)
    assert _skill_literal_offender("crossbow", names)
    assert not _skill_literal_offender("dispatch_timing", names)


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


# ---------------------------------------------------------------------------
# 6. 档位纪律：测试模块的牌堆档位必须显式（GLOSSARY『档位纪律』；retro 票 03）
# ---------------------------------------------------------------------------

TESTS_ROOT = Path(__file__).resolve().parent
"""被测树 = ``tests/`` 自身（票 32 的默认档事故就发生在这一层）。"""

DECK_PROFILE_EXEMPT = frozenset({Path("data/test_load_validation.py")})
"""豁免名单：``test_load_validation.py`` **故意**验证"未指定档位 = 产品默认档"（票 32 §1）。
它当前的裸 ``load_content_pack()`` 都在函数体内（本规则本就不扫），保留豁免是为它日后把
``PACK = load_content_pack()`` 提成模块常量时仍合法——豁免要有名单，不能靠"刚好没触发"。"""

DECK_PROFILE_FIX_HINT = "显式选档（M1 回归网 basic_53 / M2 standard_108）"
"""失败消息里的修法提示：两档即票 32 划定的 M1 回归网与产品默认档。"""


class _PackLoad(NamedTuple):
    """一条模块作用域的 ``PACK* = load_content_pack(...)``：文件、变量名、调用节点。"""

    path: Path
    name: str
    call: ast.Call


def _test_source_files() -> list[Path]:
    return sorted(TESTS_ROOT.rglob("*.py"))


def _module_scope_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """模块作用域内的节点：不下潜进函数 / 类 / lambda 的新作用域。

    ``if`` / ``try`` / ``with`` 里的赋值仍算模块级（导入时照跑），只是缩进更深。
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _module_scope_nodes(child)


def _is_load_content_pack_callee(func: ast.expr) -> bool:
    """``load_content_pack(...)`` 或 ``模块.load_content_pack(...)``。

    改名导入（``from sgs.data import load_content_pack as lcp``）是这条 AST 判据的已知盲区：
    票面只认 ``load_content_pack()`` 字面，改名绕道交给评审兜底。
    """
    if isinstance(func, ast.Name):
        return func.id == "load_content_pack"
    return isinstance(func, ast.Attribute) and func.attr == "load_content_pack"


def _module_level_pack_loads(tree: ast.Module) -> Iterator[tuple[str, ast.Call]]:
    """模块作用域里 ``PACK* = load_content_pack(...)`` 的 ``(变量名, 调用节点)``。

    ``Assign`` 与 ``AnnAssign``（``PACK: Final[...] = ...``）都算；函数体里的
    ``pack = load_content_pack(...)`` 是局部显式选择，不是会静默跟随默认档的模块常量。
    """
    for node in _module_scope_nodes(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Call) or not _is_load_content_pack_callee(value.func):
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id.startswith("PACK"):
                yield target.id, value


def _chooses_deck_profile(call: ast.Call) -> bool:
    """调用是否显式带了 ``deck_profile=`` 关键字（``**kwargs`` 不算显式）。"""
    return any(keyword.arg == "deck_profile" for keyword in call.keywords)


def _pack_loads_in(path: Path) -> list[_PackLoad]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [_PackLoad(path, name, call) for name, call in _module_level_pack_loads(tree)]


def _pack_load_violations(root: Path, exempt: frozenset[Path] = frozenset()) -> list[str]:
    """扫一棵测试树，返回"模块级 PACK 未显式选档"的违规描述（空列表 = 通过）。

    ``exempt`` 是按 ``root`` 相对路径的豁免名单。
    """
    return [
        f"{path.relative_to(root)}:{load.call.lineno} 模块级 {load.name} = load_content_pack(...) "
        f"未显式选档；{DECK_PROFILE_FIX_HINT}"
        for path in sorted(root.rglob("*.py"))
        if path.relative_to(root) not in exempt
        for load in _pack_loads_in(path)
        if not _chooses_deck_profile(load.call)
    ]


def test_module_level_pack_loads_choose_a_deck_profile_explicitly():
    """裸 ``PACK = load_content_pack()`` 会静默跟随产品默认档——票 32 一次翻档踩过 12 个文件。"""
    offenders = _pack_load_violations(TESTS_ROOT, DECK_PROFILE_EXEMPT)

    assert not offenders, "测试模块默认档会静默跟随产品默认档：" + "；".join(offenders)


def test_the_deck_profile_scan_goes_red_on_a_bare_pack_assignment(tmp_path):
    """合成反例：在临时测试树上，模块级裸赋值（含注解 / 属性调用 / ``if`` 块内）必红，
    显式档位与函数内局部赋值必绿，豁免名单照常生效——判的是文件级扫描的接线，不只判单个字符串。
    """
    sources = {
        "bare.py": "PACK = load_content_pack()\n",
        "annotated.py": "PACK: Final[ContentPack] = load_content_pack()\n",
        "attribute.py": "PACK = sgs.load_content_pack()\n",
        "scoped.py": "if True:\n    PACK = load_content_pack()\n",
        "explicit.py": 'PACK = load_content_pack(deck_profile="basic_53")\n',
        "local.py": "def f():\n    pack = load_content_pack()\n",
    }
    for name, text in sources.items():
        (tmp_path / name).write_text(text, encoding="utf-8")

    reds = "\n".join(_pack_load_violations(tmp_path))

    for red in ("bare.py", "annotated.py", "attribute.py", "scoped.py"):
        assert red in reds, f"{red} 的模块级裸赋值应被判红：{reds}"
    for green in ("explicit.py", "local.py"):
        assert green not in reds, f"{green} 不该被判红：{reds}"
    exempt = frozenset(
        Path(name) for name in ("bare.py", "annotated.py", "attribute.py", "scoped.py")
    )
    assert not _pack_load_violations(tmp_path, exempt), "豁免名单应让这些文件整体放行"


def test_the_deck_profile_scan_actually_covered_the_test_tree():
    """防"扫描目标写错 / AST 判据失效 ⇒ 空转也算通过"。"""
    files = _test_source_files()
    loads = [load for path in files for load in _pack_loads_in(path)]
    explicit = [load for load in loads if _chooses_deck_profile(load.call)]

    assert len(files) >= 20, f"只扫到 {len(files)} 个测试文件；TESTS_ROOT 可能指错了"
    assert len(explicit) >= 10, f"只认出 {len(explicit)} 个显式选档；AST 判据可能失效了"
    for relative in DECK_PROFILE_EXEMPT:
        assert (TESTS_ROOT / relative).is_file(), f"豁免名单里的文件不存在：{relative}"


# ---------------------------------------------------------------------------
# 7. 牌实例字段闭集：不含属性字段（ADR-003 的结构性表述；票 11）
# ---------------------------------------------------------------------------

CARD_REF_FORBIDDEN_ATTR_FIELDS = frozenset(
    {"attribute", "attributes", "element", "damage_type", "nature"}
)
"""属性字段的禁名闭集：**属性由效果决定、不由原牌继承**（`06` §4「决策记录（ADR）」 ADR-003）。

谁日后给牌实例加属性字段，就必须回来**重裁 ADR-003**——所以这里钉的是"结构上没有属性"，
而不是"某段代码没有读属性"。范围只到 `CardRef`：属性属于**效果**而非牌实例。
"""


def _attribute_field_offenders(fields: Iterable[str]) -> list[str]:
    """字段集合里的属性字段名（空 = 干净）。抽成函数是为了能对它单独跑合成反例。"""
    return sorted(set(fields) & CARD_REF_FORBIDDEN_ATTR_FIELDS)


def test_card_ref_has_no_attribute_field():
    from sgs.state import CardRef

    fields = set(CardRef.model_fields)
    offenders = _attribute_field_offenders(fields)

    assert not offenders, "CardRef 不得有属性字段（ADR-003：属性由效果决定）：" + "；".join(offenders)
    assert {"card_id", "type", "name"} <= fields, "CardRef 字段闭集可能读错了"
    assert len(fields) >= 9, f"只读到 {len(fields)} 个 CardRef 字段；扫描目标可能指错了"


def test_the_card_ref_attribute_scan_goes_red_on_a_new_attribute_field():
    """合成反例：给 `CardRef` 的字段集合加一个属性字段名，扫描必红。

    判的是"属性字段闭集"这条判据的接线——**没有**真的去改 `CardRef`（那会改动产品代码）。
    """
    from sgs.state import CardRef

    real = set(CardRef.model_fields)
    assert _attribute_field_offenders(real) == [], "真实 CardRef 竟然已含属性字段"

    for injected in ("attribute", "element", "damage_type"):
        offenders = _attribute_field_offenders(real | {injected})
        assert injected in offenders, f"给 CardRef 加 {injected} 应被判红：{offenders}"


# ---------------------------------------------------------------------------
# 8. 启发式 AI 的可见性硬约束（票 13 §硬约束 4；ADR-011）
# ---------------------------------------------------------------------------

HEURISTIC = SOURCE_ROOT / "agents" / "heuristic.py"
"""被测文件 = 启发式 AI 的唯一模块（票 13 §落点与结构）。"""

HEURISTIC_IMPORT_ALLOWLIST: dict[str, frozenset[str]] = {
    "__future__": frozenset({"annotations"}),
    "collections.abc": frozenset({"Callable", "Mapping", "Sequence"}),
    "types": frozenset({"MappingProxyType"}),
    # W7-03（D5）：目标集合比较与确认解析器共用一份（纯函数，不碰状态 / 规则）。
    "sgs.effects": frozenset({"Randomness", "target_sets_match"}),
    "sgs.state": frozenset(
        {
            # 协议 / 视角那一半（可见面），**不含** GameState / Player / Frame 等全知面。
            "Action",
            "ActivateSkill",
            "ArrangeCards",
            "ArrangeQuery",
            "CardRef",
            "DiscardCards",
            "DiscardQuery",
            "DodgeQuery",
            "EndPhase",
            "Pass",
            "PeachQuery",
            "PlayCard",
            "PlayPhaseQuery",
            "PlayerPublic",
            "PlayerView",
            "Query",
            "QueryKind",
            "RespondQuery",
            "SelectCards",
            "SelectCardsQuery",
            "SelectOption",
            "SelectOptionQuery",
            "SkillActivationQuery",
            "UseCard",
        }
    ),
}
"""只许 import 的**可见面**：询问 / 行动协议、视角投影、随机句柄与纯类型工具。

``sgs.engine`` / ``sgs.skills`` / ``sgs.data`` / ``sgs.resolution`` / ``sgs.timing`` 与
``sgs.state`` 里的 ``GameState`` / ``Player`` / ``Frame`` 全部**不在名单**——它们要么能读到
全知状态，要么能读到记录流。名单是**白名单**（fail-closed）：新增任何 import 都要回来显式
登记，而不是静默放行。
"""

HEURISTIC_ALLOWED_SGS_MODULES = frozenset({"sgs.effects", "sgs.state"})
"""``import sgs.x`` 形态允许的整包名（白名单之外一律判红）。"""


def _heuristic_visibility_offenders(tree: ast.Module) -> list[str]:
    """启发式 AI 的越权 import（空列表 = 只碰可见面）。抽出来是为了能对它跑合成反例。"""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入天然留在本子包内
                continue
            allowed = HEURISTIC_IMPORT_ALLOWLIST.get(node.module or "")
            if allowed is None:
                offenders.append(f"{node.lineno} from {node.module} import …")
                continue
            offenders.extend(
                f"{node.lineno} from {node.module} import {alias.name}"
                for alias in node.names
                if alias.name not in allowed
            )
        elif isinstance(node, ast.Import):
            offenders.extend(
                f"{node.lineno} import {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] == "sgs"
                and alias.name not in HEURISTIC_ALLOWED_SGS_MODULES
            )
    return offenders


def test_the_heuristic_ai_only_imports_the_visible_query_surface():
    """票 13 §硬约束 4：``agents/heuristic.py`` 不 import 任何全知状态读取路径。"""
    tree = ast.parse(HEURISTIC.read_text(encoding="utf-8"), filename=str(HEURISTIC))

    offenders = _heuristic_visibility_offenders(tree)

    assert not offenders, "启发式 AI 碰到了全知面：" + "；".join(offenders)


def test_the_heuristic_import_scan_is_not_vacuous():
    """防"扫描目标写错 / 解析失败 ⇒ 空转也算通过"。"""
    tree = ast.parse(HEURISTIC.read_text(encoding="utf-8"), filename=str(HEURISTIC))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "Query" in imported and "PlayerView" in imported, "扫描目标可能指错了"
    assert {"GameState", "Player", "Frame"}.isdisjoint(imported)


def test_the_heuristic_import_scan_goes_red_on_an_omniscient_import():
    """合成反例：全知面（``GameState`` / ``sgs.engine`` / ``sgs.skills``）一律判红。"""
    red = ast.parse(
        "from sgs.state import GameState\n"
        "import sgs.engine\n"
        "from sgs.skills import CATALOG\n"
        "from sgs.timing import Timing\n"
    )
    green = ast.parse(
        "from sgs.state import Action, Query, PlayerView\n"
        "from sgs.effects import Randomness\n"
        "from .participants import Participant\n"
    )

    reds = "\n".join(_heuristic_visibility_offenders(red))
    for needle in ("GameState", "sgs.engine", "sgs.skills", "sgs.timing"):
        assert needle in reds, f"{needle} 应被判红：{reds}"
    assert not _heuristic_visibility_offenders(green), "可见面 import 不该被判红"
