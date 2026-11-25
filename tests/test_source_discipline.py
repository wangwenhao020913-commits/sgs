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

from sgs.engine import rng as engine_rng

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


def test_a_substream_name_lives_only_as_a_constant_in_the_rng_module():
    """**子流名**只许以 ``*_SUBSTREAM`` 常量的形式住在 ``engine/rng.py``（框架票 03）。

    子流 = 唯一随机源按用途切开的独立序列（``GLOSSARY.md`` §子流）；名字一旦手写在调用点，
    "这个项目有几条流、各自叫什么"就没有单点答案，两条语义不同的流也容易被顺手合并
    （票 01 §7.3 的反例：复用 ``shuffle`` 抽牌，会让整副牌在每次抽签后换序）。

    判据 = 该常量的**字面量**不出现在任何其他源文件里：调用方只拿工厂
    （``hero_cards_stream`` / ``deck_search_stream``），不拿名字。
    """
    names = {
        name: value
        for name, value in vars(engine_rng).items()
        if name.endswith("_SUBSTREAM")
    }

    assert names, "前置：rng 模块里应当有 *_SUBSTREAM 常量（否则本扫描是空转）"
    assert names.get("DECK_SEARCH_SUBSTREAM") == "deck_search", (
        "框架票 03 新增的「按牌名从牌堆取一张」子流名"
    )
    assert "deck_search_stream" in engine_rng.__all__, "工厂要和常量一起导出"

    offenders = []
    for path in _source_files():
        if path == RNG_MODULE:
            continue
        text = path.read_text(encoding="utf-8")
        for name, value in names.items():
            if f'"{value}"' in text or f"'{value}'" in text:
                offenders.append(f"{_relative(path)} 手写了子流名 {value!r}（{name}）")

    assert not offenders, "子流名只许住在 engine/rng.py：" + "；".join(offenders)


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
    # ai-view 票 02：``Score.tracing`` 的上下文管理与 trace 的类型标注——两个都是纯标准库，
    # 不碰任何游戏状态（与本扫描要拦的"全知面"无关）。
    "collections.abc": frozenset({"Callable", "Iterator", "Mapping", "Sequence"}),
    "contextlib": frozenset({"contextmanager"}),
    "types": frozenset({"MappingProxyType"}),
    # W7-03（D5）：目标集合比较与确认解析器共用一份（纯函数，不碰状态 / 规则）。
    # M5-07：AI 按**行为表**认【杀】族（火 / 雷 / 转化与虚拟产物），不抄牌名集合。
    "sgs.effects": frozenset(
        {
            "Randomness",
            "behavior_for",
            # yijiang-2014-round2 票 21：不枚举档多选面的**形状谓词**（引擎收答案时用的是
            # 同一份）。它只读询问自带的 `options` / `disabled` / 区间 ⇒ 可见面，不是全知
            # 状态；AI 自建集合必须过它，"AI 与引擎同一份合法性"才成立。
            "is_legal_option_set",
            # 评审 P2-a：谓词的两半（可选项池 / 可达上限）抽出来由谓词共读。同样只读询问
            # 自带的四个字段 ⇒ 与 `is_legal_option_set` 同一条可见面，不是全知状态。
            "cap_of",
            "usable_options",
            "is_slash",
            "target_sets_match",
            # ai-view 票 01：`behavior_for(card).target_rule.count` 的**枚举**（三值：额定 /
            # 全体存活 / 全体存活含自己）。与已允许的 `behavior_for` 同源、同一个读点——
            # 主公 veto 用它在"挑目标"与"目标由牌面强制"之间分界，不另抄一张牌名表。
            "TargetCount",
        }
    ),
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
            # 神周瑜移动版【业炎】的计数式分配（huo-shenzhouyu-mobile 票 05）：候选构造子 +
            # 询问模型，都是**可见面**（与已允许的 SelectOption / SelectOptionQuery 同源）。
            "Distribute",
            "DistributeQuery",

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
            "Reforge",
            "RespondQuery",
            "SelectCards",
            "SelectCardsQuery",
            "SelectOption",
            "SelectOptionQuery",
            # jie-feng-batch2 票 01a：多选选项面（界夏侯渊【神速】）——同样是询问模型。
            "SelectOptions",
            "SelectOptionsQuery",
            "SkillActivationQuery",
            # huo-dianwei-mobile 票 01：`CardRef.slot` 的枚举（**可见面**的一部分，
            # 与已允许的 CardRef 同源；不是全知状态 / 规则表）。
            "SlotKind",
            # feng-zhangjiao 票 02：改判窗口的候选构造子（与 PlayCard 同为可见动作面）。
            "SubstituteJudgeCard",
            "UseCard",
            # 票 18（D1）：无懈窗口的 `RespondQuery.cause` 就是它；AI 按 `NULLIFY` 分派
            # 目标化判据。它是询问自带面的一部分，不是全知状态。
            "WindowCause",
            # `ai-view` 票 14：`Slot.ref.zone` 的枚举（**可见面**的一部分，与已允许的
            # CardRef / SlotKind 同源）——拆 / 顺按"别人的装备区 / 判定区"换价要读它；
            # 区域坐标是询问自带的面，不是全知状态。
            "Zone",
            # 票 13 的评审 R3：步骤键的**唯一拼法**（`SELECT_OPTION` 的 `skill_id` 形状）。
            # 纯字符串函数、不读任何状态；放在这里是为了让引擎（流水线）与 AI（分组表）
            # 拼同一处，而不是各抄一遍 `f"{short}:{step}"`。
            "step_option_key",
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


# ---------------------------------------------------------------------------
# 9. 身份信念模块的可见面（`ai-view` 票 07）
# ---------------------------------------------------------------------------

IDENTITY = SOURCE_ROOT / "agents" / "identity.py"
"""`ai-view` 票 07 的身份信念模块：**启发式经相对 import 使用它**，故它自己也要被扫。

启发式白名单对相对 import 一律跳过（``if node.level: continue``）——那对同子包的
participants 是对的，但 `from . import identity` 把 identity 的 import 面带进了启发式的
传递闭包。若只靠"相对导入天然安全"，启发式那句"不 import ``sgs.data``"就成了假命题；
这一节的 allowlist 是同一份纪律的**第二张表**（fail-closed：未登记即红）。
"""

IDENTITY_IMPORT_ALLOWLIST: dict[str, frozenset[str]] = {
    "__future__": frozenset({"annotations"}),
    "math": frozenset(),
    "collections.abc": frozenset({"Mapping", "Sequence"}),
    "dataclasses": frozenset({"dataclass"}),
    "functools": frozenset({"lru_cache"}),
    "types": frozenset({"MappingProxyType"}),
    "typing": frozenset({"Any"}),
    # 配比表是规则常数（与 `_IDENTITY_ROSTER` 同一份数据的真源）；PlayerView 是询问自带的
    # 视图类型。两者都是可见面，不含 GameState / 记录流 / 技能目录。
    "sgs.data": frozenset({"IDENTITY_ROSTERS", "Identity"}),
    "sgs.state": frozenset({"PlayerView"}),
}

IDENTITY_ALLOWED_SGS_MODULES = frozenset({"sgs.data", "sgs.state"})
"""``import sgs.x`` 形态允许的整包名（与启发式的同一条口径）。"""


def _identity_visibility_offenders(tree: ast.Module) -> list[str]:
    """身份信念模块的越权 import（空列表 = 只碰可见面）。抽出来是为了跑合成反例。

    判据与第十节**共用** :func:`_import_offenders`：同一套规则、两张 allowlist。三份
    差不多一样的扫描器是同一个 bug 的第三份拷贝（票 08 的教训）。
    """
    return _import_offenders(
        tree, IDENTITY_IMPORT_ALLOWLIST, IDENTITY_ALLOWED_SGS_MODULES
    )


def test_the_identity_module_only_imports_the_visible_surface():
    """`ai-view` 票 07：identity 只读配比表 + ``PlayerView``，不碰全知面。"""
    tree = ast.parse(IDENTITY.read_text(encoding="utf-8"), filename=str(IDENTITY))

    offenders = _identity_visibility_offenders(tree)

    assert not offenders, "身份信念模块碰到了全知面：" + "；".join(offenders)


def test_the_identity_import_scan_goes_red_on_an_omniscient_import():
    """合成反例：``GameState`` / ``sgs.engine`` / ``sgs.skills`` 一律判红。"""
    red = ast.parse(
        "from sgs.state import GameState, PlayerView\n"
        "import sgs.engine\n"
        "from sgs.skills import CATALOG\n"
    )
    green = ast.parse(
        "from sgs.data import IDENTITY_ROSTERS, Identity\n"
        "from sgs.state import PlayerView\n"
        "from .participants import Participant\n"
    )

    reds = "\n".join(_identity_visibility_offenders(red))
    for needle in ("GameState", "sgs.engine", "sgs.skills"):
        assert needle in reds, f"{needle} 应被判红：{reds}"
    assert not _identity_visibility_offenders(green), "可见面 import 不该被判红"


# ---------------------------------------------------------------------------
# 10. 特征 / 权重模块：B 族**不许另算**（`ai-view` 票 08a）
# ---------------------------------------------------------------------------

FEATURES = SOURCE_ROOT / "agents" / "features.py"
"""三族特征提取（票 08a）。它进启发式的传递闭包要等 08b 接线，故**现在就**扫。

与 identity 那一节同姿态：可见面白名单 + 一条本模块特有的纪律。这一节的第二条是本票的
验收判据——"``features.py`` 里不得出现距离 / 斩杀 / 记牌的重新实现"。判据落成**闭集**
（函数名白名单 + 局面量的实现名黑名单）而不是"看一眼"：委托与重写用的是同一批名字，
出现即回来评审。
"""

WEIGHTS = SOURCE_ROOT / "agents" / "weights.py"
"""权重结构与先验中心（票 08a）：纯数据 + 键语法 + 锚，故它的白名单比 features 更窄。"""

TRAIN_SERVE_IMPORT_ALLOWLIST: dict[str, frozenset[str]] = {
    "__future__": frozenset({"annotations"}),
    "collections.abc": frozenset({"Iterable", "Mapping", "Sequence"}),
    "dataclasses": frozenset({"dataclass"}),
    "functools": frozenset({"lru_cache"}),
    "math": frozenset(),
    "types": frozenset({"MappingProxyType"}),
    # 关系类的闭集要拿身份（``Identity``）与模式（``Mode``）比对；两者都是**规则常数**，
    # 不是状态 / 记录流。``PlayerView`` / ``PublicEvent`` / ``Action`` / ``Query`` 是询问
    # 自带的视图、公开事实与协议类型。
    "sgs.data": frozenset({"Identity", "Mode"}),
    "sgs.state": frozenset({"Action", "PlayerView", "PublicEvent", "Query"}),
}
"""特征 / 权重模块允许的**非相对** import（与启发式的可见面同一条口径）。"""

TRAIN_SERVE_ALLOWED_SGS_MODULES = frozenset({"sgs.data", "sgs.state"})
"""``import sgs.x`` 形态允许的整包名（``sgs.engine`` / ``sgs.skills`` 一律判红）。"""

LAZY_ONLY_MODULE_IMPORTS = frozenset({"heuristic"})
"""相对 import 里**只许在函数体内**出现的那几个（08b 起 ``heuristic`` → 这两个模块单向依赖）。

顶层 ``from . import heuristic`` 会立刻成环；函数内的延迟 import 是这条边的唯一出口，两个
模块的 docstring 都写了它存在的理由（A 族的锚 / ``Score`` / 敌我判据）。
"""

FEATURES_FUNCTIONS = frozenset(
    {
        "relation_class",
        "_relation",
        "_group",
        "b_family",
        "c_family",
        "a_family",
        "_involved_seats",
        "frame",
        "_scored_items",
        "extract",
        "candidate_score",
        "unweighted_candidate_score",
        "position_score",
        "linear",
    }
)
"""``features.py`` 的**顶层函数闭集**：加一个就回来评审（"没有第二套局面量实现"的机械面）。"""

FEATURES_METHODS = frozenset({"b", "position", "as_mapping"})
"""``FeatureFrame`` 的方法闭口（帧上的读口只有这三个；没有消费者的不加）。"""

BOARD_QUANTITY_NAMES = frozenset(
    {
        "distance",
        "lethal",
        "damage_to",
        "has_card_probability",
        "dodge_probability",
        "flash_probability",
        "unknown_pool",
        "unknown_card_expectation",
        "conserved_card_count",
        "visible_card_faces",
        "verified_deck_profile",
        "expected_damage",
        "card_value",
    }
)
"""**局面量**的实现名（``heuristic.py`` 里那几个算子的名字，去掉前导下划线）。

B 族的值必须来自既有打分器的 trace（同一个函数），故这些名字出现在特征模块里
**一律回来评审**：委托与重写用的是同一批名字，"不许另算"的判据因此是同一条。
"""


def _import_offenders(
    tree: ast.Module,
    allowlist: dict[str, frozenset[str]],
    allowed_sgs_modules: frozenset[str],
) -> list[str]:
    """这棵树的**非相对** import 越权项（空列表 = 只碰可见面）。抽出来是为了跑合成反例。"""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入留在本子包内（延迟 import 由下一条断言管）
                continue
            allowed = allowlist.get(node.module or "")
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
                and alias.name not in allowed_sgs_modules
            )
    return offenders


def _module_level_relative_imports(tree: ast.Module) -> list[tuple[str, int]]:
    """**模块顶层**（不在任何函数里）的相对 import：``[(模块名, 行号)]``。

    只看``tree.body``的直系语句：``if`` / ``try`` 里包一层仍然是"导入模块时就执行"，
    但这两个模块里没有那种写法，出现时这条会漏——漏的那一半由"闭集"的两条断言兜住。
    """
    found: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level:
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
    return found


def _lazy_import_offenders(tree: ast.Module) -> list[str]:
    """顶层 import 了"只许延迟 import"的模块 ⇒ 一条违规（08b 起会成环）。"""
    return [
        f"{lineno} 顶层 import 了 {name}（只许在函数体内延迟 import）"
        for name, lineno in _module_level_relative_imports(tree)
        if name in LAZY_ONLY_MODULE_IMPORTS
    ]


def _function_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """``(顶层函数名, 类方法名)``。"""
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef)
    functions = {node.name for node in tree.body if isinstance(node, definitions)}
    methods = {
        child.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        for child in node.body
        if isinstance(child, definitions)
    }
    return functions, methods


def _board_quantity_offenders(source: str) -> list[str]:
    """源码里出现的**局面量实现名**（空列表 = 干净）。抽出来是为了能对它跑合成反例。"""
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Name):
            name = node.id
        if name is not None and name.lstrip("_") in BOARD_QUANTITY_NAMES:
            offenders.append(f"{getattr(node, 'lineno', '?')} {name}")
    return offenders


def test_the_feature_and_weight_modules_only_import_the_visible_surface():
    """`ai-view` 票 08a：两个新模块只碰可见面（视角 / 询问 / 规则常数）。"""
    for path in (FEATURES, WEIGHTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders = _import_offenders(
            tree, TRAIN_SERVE_IMPORT_ALLOWLIST, TRAIN_SERVE_ALLOWED_SGS_MODULES
        )

        assert not offenders, f"{path.name} 碰到了全知面：" + "；".join(offenders)


def test_the_feature_and_weight_modules_import_the_heuristic_only_lazily():
    """启发式只许**函数内**延迟 import：08b 起 ``heuristic → features/weights`` 是单向的。"""
    for path in (FEATURES, WEIGHTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders = _lazy_import_offenders(tree)

        assert not offenders, f"{path.name}：" + "；".join(offenders)


def test_the_feature_module_defines_no_board_quantity_of_its_own():
    """**B 族不许另算**：``features.py`` 里没有距离 / 斩杀 / 记牌 / 概率的第二次实现。"""
    source = FEATURES.read_text(encoding="utf-8")
    offenders = _board_quantity_offenders(source)

    assert not offenders, (
        "特征模块里出现了局面量的实现："
        + "；".join(offenders)
        + "（B 族的值必须来自既有打分器的 trace，不许另算一份）"
    )
    assert len(FEATURES_FUNCTIONS) >= 10, "函数闭集退化了；判据可能失效"


def test_the_feature_module_function_set_is_closed():
    """函数闭集：``features.py`` 的顶层函数与 ``FeatureFrame`` 方法**一个不多**。

    加一个函数 = 回来评审"它算的是既有实现已经算过的东西吗"。这条与上一条是同一份纪律的
    两半：上一条按**名字**拦已知的局面量，这一条按**清单**拦"换了个名字的新算子"。
    """
    tree = ast.parse(FEATURES.read_text(encoding="utf-8"), filename=str(FEATURES))
    functions, methods = _function_names(tree)

    assert functions == FEATURES_FUNCTIONS, (
        f"features.py 的函数集变了：多 {sorted(functions - FEATURES_FUNCTIONS)} / "
        f"少 {sorted(FEATURES_FUNCTIONS - functions)}"
    )
    assert methods == FEATURES_METHODS, (
        f"FeatureFrame 的方法集变了：多 {sorted(methods - FEATURES_METHODS)} / "
        f"少 {sorted(FEATURES_METHODS - methods)}"
    )


def test_the_board_quantity_scan_goes_red_on_a_second_implementation():
    """合成反例：在特征模块里写一份"距离 / 斩杀 / 记牌"就必红（判据接线没坏）。"""
    red = (
        "def _distance(a, b):\n"
        "    return min((a - b) % 8, (b - a) % 8)\n"
        "\n"
        "def lethal(target):\n"
        "    return target.hp <= 1\n"
    )
    green = (
        "def relation_class(view, seat):\n"
        "    return 'self' if seat == view.seat else 'unknown'\n"
    )

    reds = "\n".join(_board_quantity_offenders(red))
    for needle in ("_distance", "lethal"):
        assert needle in reds, f"{needle} 应被判红：{reds}"
    assert not _board_quantity_offenders(green), "干净的特征函数不该被判红"


def test_the_import_and_lazy_scans_catch_a_red_module():
    """合成反例：顶层 ``from . import heuristic`` 与 ``sgs.engine`` 都必须判红。"""
    red = ast.parse(
        "from . import heuristic\n"
        "from sgs.state import GameState\n"
        "import sgs.skills\n"
    )
    green = ast.parse(
        "from sgs.data import Identity, Mode\n"
        "from sgs.state import PlayerView, Query\n"
        "from . import weights\n"
    )

    assert _lazy_import_offenders(red), "顶层 import 启发式应被判红"
    assert _import_offenders(
        red, TRAIN_SERVE_IMPORT_ALLOWLIST, TRAIN_SERVE_ALLOWED_SGS_MODULES
    ), "sgs.skills / GameState 应被判红"
    assert not _lazy_import_offenders(green), "``from . import weights`` 是允许的"
    assert not _import_offenders(
        green, TRAIN_SERVE_IMPORT_ALLOWLIST, TRAIN_SERVE_ALLOWED_SGS_MODULES
    ), "可见面 import 不该被判红"
