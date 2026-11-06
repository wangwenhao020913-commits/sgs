"""**否定分类四层**的审计清单（M6 票 07；spec D15 / S8）。

规则源：`三国杀详细规则.txt` §十九 L2144–L2148（【奋威】）、§十九 L2212–L2216
（【离间】）；设计裁定 = spec D15「否定类不统一，按牌面分类，只建审计清单」。

四层**各自实现、不共用类型**（票面"不做统一抽象"）：

.. list-table::
   :header-rows: 1

   * - 层
     - 语义
     - M6 载体
   * - 目标产生前取消
     - 根本不会被选为目标：目标集合在指定阶段就被剔除
     - 无（贾诩【帷幕】不在本批；既有实现是【空城】/【谦逊】）
   * - 目标级无效
     - 目标照常指定，但不产生效果
     - 界甘宁【奋威】（本票）
   * - 事件帧无效
     - 某个**事件帧**无效
     - 票 08 的【奇才】②（本票只登记形状 + 构造测试）
   * - 响应窗口不产生
     - 不可无懈 + 不可打出对应牌
     - 界貂蝉【离间】（本票）

本文件把"**实现站点的代码落点**"变成一张机械可核的清单：与
``tests/test_card_family_sites.py`` 同姿态——新增一个未登记的否定站点**判红**。

扫描口径（AST，只读源码、不 import ``src/sgs`` 以免运行时副作用污染）：

- **站点** = 某层自己的**机制符号**在 ``src/sgs`` 里的一次出现，按
  ``(相对路径, 所属函数 / 类 / 模块级常量, 机制符号, 归一化源码行)`` 归组计数；
- 每个站点都必须登记进 :data:`SITES`，并二选一：归到四层之一，或显式登记为
  :data:`NOT_NEGATION` / :data:`MECHANISM`（写明理由）；计数不符 / 条目消失 / 未登记
  符号都判红；
- 四层的键 = :data:`LAYERS` 的键，**闭集**（D15 只给这四层）；新增第五层必须改这里。

**事件帧无效的形状**：机制符号登记为 ``EVENT_FRAME_INVALID``——一个**事件帧自己**的
帧私有失效标志。票 07 只登记形状（站点数 = 0），**票 08 把它变成第一个活站点**
（界黄月英【奇才】②：机制定义 / 写入 / 读取三处站点，见 :data:`SITES`）。
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sgs.data import hero_skill_id
from sgs.skills import CATALOG

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "sgs"
TESTS_ROOT = REPO_ROOT / "tests"

# 四层各自的**机制符号**（每层自己的实现物，不共用；D15 的"不统一抽象"落成这条登记）。
TARGET_POOL_TOKEN = "TargetPoolSkill"
EFFECTIVENESS_TOKEN = "ModifierPoint.EFFECTIVENESS"
TARGET_INVALID_WRITE_TOKEN = "mark_targets_ineffective"
TARGET_INVALID_READ_TOKEN = "target_is_ineffective"
EVENT_FRAME_TOKEN = "EVENT_FRAME_INVALID"
UNRESPONSIVE_TOKEN = "Tag.UNRESPONSIVE"

TOKEN_KIND: dict[str, str] = {
    # 机制符号 → 它作为**哪种 AST 节点**出现才算一次站点（防止 Call 与其 func Name 重复计数）
    TARGET_POOL_TOKEN: "call",
    EFFECTIVENESS_TOKEN: "attr",
    TARGET_INVALID_WRITE_TOKEN: "call",
    TARGET_INVALID_READ_TOKEN: "call",
    EVENT_FRAME_TOKEN: "name",
    UNRESPONSIVE_TOKEN: "attr",
}
"""机制符号 → 计数节点类型（``call`` / ``attr`` / ``name``）。"""

MECHANISM = "mechanism"
"""不是站点、而是**机制读取方**的登记（引擎里按层消费符号的那几处）。"""

NOT_NEGATION = "not-negation"
"""用同一机制符号但**不是四层否定**的站点（如【青釭剑】无视防具）。"""


@dataclass(frozen=True)
class Layer:
    """四层之一：语义 / 本批载体 / 现状 / 本层的机制符号 / 行为面锚点。"""

    semantics: str
    carrier: str | None
    status: str
    existing_carriers: tuple[str, ...]
    tokens: tuple[str, ...]
    tests: tuple[str, ...]


NO_CARRIER = "no-carrier"
ORDERED_ELSEWHERE = "ordered-elsewhere"
LIVE = "live"

LAYERS: dict[str, Layer] = {
    "target-creation-cancelled": Layer(
        semantics="根本不会被选为目标：目标集合在指定目标阶段就被剔除（指定目标与后续发动一并取消）",
        carrier=None,
        status=NO_CARRIER,
        existing_carriers=(
            hero_skill_id("zhugeliang", "kongcheng"),
            hero_skill_id("luxun", "qianxun"),
        ),
        tokens=(TARGET_POOL_TOKEN,),
        tests=(
            "test_kongcheng_without_hand_cards_leaves_the_slash_pool",
            "test_qianxun_leaves_the_snatch_and_indulgence_pools_even_with_hand_cards",
        ),
    ),
    "target-level-invalid": Layer(
        semantics="目标照常指定，但不产生效果（自己的“使用结算开始时”终止结算、没有生效前的响应窗口）",
        carrier=hero_skill_id("jie_ganning", "fenwei"),
        status=LIVE,
        existing_carriers=(
            "equip.renwang_shield",
            "equip.vine_armor",
        ),
        tokens=(
            EFFECTIVENESS_TOKEN,
            TARGET_INVALID_WRITE_TOKEN,
            TARGET_INVALID_READ_TOKEN,
        ),
        tests=("test_fenwei_invalidates_only_the_chosen_subset",),
    ),
    "event-frame-invalid": Layer(
        semantics="某个事件帧无效：事件已经发生、在它自己的帧里被取消",
        # M6 票 08 的载体（本票落地：票 07 只登记形状，票 08 补站点与行为测试）
        carrier=hero_skill_id("jie_huangyueying", "qicai"),
        status=LIVE,
        existing_carriers=(),
        tokens=(EVENT_FRAME_TOKEN,),
        tests=(
            "test_jie_qicai_cancels_another_characters_discard_of_your_armor",
            "test_jie_qicai_does_not_cancel_a_weapon_discard",
        ),
    ),
    "response-window-not-produced": Layer(
        semantics="响应窗口不产生：不可无懈 + 不可打出对应牌（窗口该不该存在仍由牌的规则决定）",
        carrier=hero_skill_id("jie_diaochan", "lijian"),
        status=LIVE,
        existing_carriers=(
            hero_skill_id("jie_machao", "tieqi"),
            hero_skill_id("huangzhong", "liegong"),
        ),
        tokens=(UNRESPONSIVE_TOKEN,),
        tests=(
            "test_jie_lijian_produces_no_nullify_and_no_slash_response_window",
            "test_tieqi_red_hides_the_whole_dodge_window",
            "test_tieqi_red_on_the_second_target_also_seals_its_window",
            "test_liegong_also_seals_the_eight_diagrams_provider",
        ),
    ),
}
"""四层（D15 的**闭集**）：语义 / 本批载体 / 现状 / 机制符号 / 行为面锚点。"""


@dataclass(frozen=True)
class Rule:
    """一条登记：``layer`` = 四层键 / :data:`MECHANISM` / :data:`NOT_NEGATION`。"""

    layer: str
    count: int
    reason: str


Site = tuple[str, str, str, str]
"""``(相对路径, 所属函数 / 类 / 模块级常量, 机制符号, 归一化源码行)``。"""


# ---------------------------------------------------------------------------
# 扫描器
# ---------------------------------------------------------------------------


def _dotted(node: ast.AST) -> str:
    """``Name`` / ``Attribute`` 链 → 点分名（``Tag.UNRESPONSIVE`` / ``ModifierPoint.EFFECTIVENESS``）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _owner_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _owner(node: ast.AST, tree: ast.AST) -> str:
    """站点所属的**稳定名字**：函数 / 类的点分名；模块级取最近的赋值目标名。"""
    parent = _owner_map(tree)
    parts: list[str] = []
    cursor = node
    while cursor in parent:
        holder = parent[cursor]
        if isinstance(holder, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(holder.name)
        cursor = holder
    if parts:
        return ".".join(reversed(parts))
    cursor = node
    while cursor in parent:
        holder = parent[cursor]
        if isinstance(holder, ast.Assign) and isinstance(holder.targets[0], ast.Name):
            return holder.targets[0].id
        if isinstance(holder, ast.AnnAssign) and isinstance(holder.target, ast.Name):
            return holder.target.id
        cursor = holder
    return "<module>"


def _mechanism_symbol(node: ast.AST) -> tuple[str, str] | None:
    """这个 AST 节点是不是一个**机制符号出现**；是 ⇒ ``(节点类型, 符号)``。

    一个出现只算一次：调用节点只按被调用名判（不再把 ``Call.func`` 的 Name 算一遍），
    属性节点只按属性链判，裸名字只按名字判（事件帧标志就是裸名字常量）；``TOKEN_KIND``
    把"哪种节点类型才算"写死，故 `Tag.UNRESPONSIVE` 的读取与写入各算一次、不重不漏。
    """
    if isinstance(node, ast.Call):
        return "call", _dotted(node.func)
    if isinstance(node, ast.Attribute):
        return "attr", _dotted(node)
    if isinstance(node, ast.Name):
        return "name", node.id
    return None


def _tokens() -> frozenset[str]:
    return frozenset(token for layer in LAYERS.values() for token in layer.tokens)


def scan_source(source: str, path: str) -> Counter[Site]:
    """扫一份源码，返回 ``{站点: 次数}``。"""
    tree = ast.parse(source)
    tokens = _tokens()
    lines = source.splitlines()
    hits: Counter[Site] = Counter()
    for node in ast.walk(tree):
        found = _mechanism_symbol(node)
        if found is None:
            continue
        kind, name = found
        if name not in tokens or TOKEN_KIND[name] != kind:
            continue
        snippet = " ".join(lines[node.lineno - 1].split())
        hits[(path, _owner(node, tree), name, snippet)] += 1
    return hits


def scan_tree(root: Path = SRC_ROOT) -> Counter[Site]:
    """扫整个源码树（``.py``，跳过 ``__pycache__``）。"""
    hits: Counter[Site] = Counter()
    for file in sorted(root.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        rel = file.relative_to(REPO_ROOT).as_posix()
        hits.update(scan_source(file.read_text(encoding="utf-8"), rel))
    return hits


def audit(hits: Counter[Site], sites: dict[Site, Rule]) -> list[str]:
    """把扫描结果与登记表对照；返回**全部**问题（不 fail-fast，一次看全）。"""
    problems: list[str] = []
    for site, count in sorted(hits.items()):
        rule = sites.get(site)
        label = f"{site[0]} :: {site[1]} :: {site[2]} :: {site[3]}"
        if rule is None:
            problems.append(
                f"未登记的否定站点：{label}（出现 {count} 次）——"
                "登记进 SITES 并归到四层之一（或显式写清为什么不是否定）"
            )
        elif rule.layer not in LAYERS and rule.layer not in (MECHANISM, NOT_NEGATION):
            problems.append(f"{label} 的归类 {rule.layer!r} 不是四层 / {MECHANISM!r} / {NOT_NEGATION!r}")
        elif not rule.reason.strip():
            problems.append(f"{label} 缺少一行理由")
        elif rule.count != count:
            problems.append(
                f"{label} 的站点数变了：登记 {rule.count} 处，实得 {count} 处——"
                "这多半是同一处又加了一行否定机制（或删了一处）"
            )
    for site in sorted(set(sites) - set(hits)):
        problems.append(
            f"登记条目已不存在：{site[0]} :: {site[1]} :: {site[2]} :: {site[3]}——"
            "函数改名 / 代码搬走时请同步这份清单"
        )
    return problems


# ---------------------------------------------------------------------------
# 登记表（当前全树 15 处 / 13 组；M6 票 17 之后的真实状态）
# ---------------------------------------------------------------------------

SITES: dict[Site, Rule] = {
    # ---- 第 1 层：目标产生前取消（既有载体【空城】/【谦逊】；M6 无新载体）----
    (
        "src/sgs/skills/target_pool.py",
        "TARGET_POOL_SKILLS",
        TARGET_POOL_TOKEN,
        "TargetPoolSkill(",
    ): Rule(
        "target-creation-cancelled",
        3,
        "C 族三条『不能成为目标』声明（【空城】/【谦逊】/界【空城】）；M6 的【帷幕】不在本批",
    ),
    # ---- 第 2 层：目标级无效（【奋威】本票 + 既有【仁王盾】/【藤甲】）----
    (
        "src/sgs/skills/catalog.py",
        "_SKILL_DEFS",
        EFFECTIVENESS_TOKEN,
        "modifiers=(Modifier(ModifierPoint.EFFECTIVENESS, _renwang_effectiveness),),",
    ): Rule(
        "target-level-invalid",
        1,
        "【仁王盾】黑色【杀】对装备者无效：既有的目标级无效（查询期修饰器）",
    ),
    (
        "src/sgs/skills/catalog.py",
        "_SKILL_DEFS",
        EFFECTIVENESS_TOKEN,
        "Modifier(ModifierPoint.EFFECTIVENESS, _vine_armor_effectiveness),",
    ): Rule(
        "target-level-invalid",
        1,
        "【藤甲】普通【杀】/ 南蛮 / 万箭对装备者无效：既有的目标级无效",
    ),
    (
        "src/sgs/skills/negation.py",
        "_fenwei_handler",
        TARGET_INVALID_WRITE_TOKEN,
        "mark_targets_ineffective(frame, tuple(chosen))",
    ): Rule(
        "target-level-invalid",
        1,
        "【奋威】把『本次使用对其中任意名目标无效』写进使用帧（本票的唯一写入口）",
    ),
    (
        "src/sgs/skills/hero_lei.py",
        "fuyin_effect",
        TARGET_INVALID_WRITE_TOKEN,
        "mark_targets_ineffective(frame, (owner,))",
    ): Rule(
        "target-level-invalid",
        1,
        "诸葛瞻【父荫】『此牌对你无效』= 目标级无效（B6『成为目标后』写入、C1 读取）；"
        "与【奋威】同一写入口，触发者换成目标自己（锁定技）",
    ),
    (
        "src/sgs/engine/steps.py",
        "resolve_start",
        TARGET_INVALID_READ_TOKEN,
        "target_is_ineffective(frame, current)",
    ): Rule(
        "target-level-invalid",
        1,
        "结算 C1 的读取口：与『牌面 / 修饰器判无效』共用同一个终止点",
    ),
    # ---- 第 3 层：事件帧无效（M6 票 08 的【奇才】② = 本层第一个活站点）----
    # 三个站点 = 机制符号的**定义 / 写入 / 读取**各一处；它们同住 resolution/events.py，
    # 由技能层（limits.py 的接缝回调）与引擎（move_in_frame）分别消费。
    (
        "src/sgs/resolution/events.py",
        "EVENT_FRAME_INVALID",
        EVENT_FRAME_TOKEN,
        'EVENT_FRAME_INVALID = "event_frame_invalid"',
    ): Rule(
        "event-frame-invalid",
        1,
        "事件帧无效的**机制符号本身**（帧私有槽名）：本层唯一的类型定义处",
    ),
    (
        "src/sgs/resolution/events.py",
        "invalidate_event_frame",
        EVENT_FRAME_TOKEN,
        "frame.data[EVENT_FRAME_INVALID] = True",
    ): Rule(
        "event-frame-invalid",
        1,
        "唯一**写入口**：界黄月英【奇才】② 判定成立后把事件帧标成无效",
    ),
    (
        "src/sgs/resolution/events.py",
        "take_event_frame_invalid",
        EVENT_FRAME_TOKEN,
        "return bool(frame.data.pop(EVENT_FRAME_INVALID, False))",
    ): Rule(
        "event-frame-invalid",
        1,
        "唯一**读取口**：引擎在装备区弃置的接缝派发之后消费一次（取消整次移动）",
    ),
    # ---- 第 4 层：响应窗口不产生（【离间】本票 + 既有【铁骑】/ 界【铁骑】）----
    (
        "src/sgs/skills/active.py",
        "_lijian_effect",
        UNRESPONSIVE_TOKEN,
        "add_tag(duel, Tag.UNRESPONSIVE)",
    ): Rule(
        "response-window-not-produced",
        1,
        "界貂蝉【离间】给虚拟【决斗】挂全场档『不可响应』（本票）",
    ),
    (
        "src/sgs/skills/response_window.py",
        "_tieqi",
        UNRESPONSIVE_TOKEN,
        "add_tag(frame, Tag.UNRESPONSIVE, target=targets[cursor])",
    ): Rule(
        "response-window-not-produced",
        1,
        "【铁骑】判定红 ⇒ 该目标不能使用【闪】响应此【杀】（既有的逐目标档）",
    ),
    (
        "src/sgs/skills/response_window.py",
        "_jie_tieqi",
        UNRESPONSIVE_TOKEN,
        "add_tag(frame, Tag.UNRESPONSIVE, target=target)",
    ): Rule(
        "response-window-not-produced",
        1,
        "界【铁骑】：付不起同花色牌 / 选择不弃 ⇒ 不能使用【闪】响应（票 14 把原两条分支并入同一处无条件调用，站点数 2 → 1）",
    ),
    (
        "src/sgs/skills/response_window.py",
        "_liegong",
        UNRESPONSIVE_TOKEN,
        "add_tag(frame, Tag.UNRESPONSIVE, target=target)",
    ): Rule(
        "response-window-not-produced",
        1,
        "【烈弓】满足条件的目标（手牌数 ≥ 体力值 或 ≤ 攻击范围）不能使用【闪】响应（票 03；逐目标档）",
    ),
    (
        "src/sgs/skills/life_after.py",
        "_zhaxiang_unresponsive",
        UNRESPONSIVE_TOKEN,
        "add_tag(frame, Tag.UNRESPONSIVE)",
    ): Rule(
        "response-window-not-produced",
        1,
        "界黄盖【诈降】：本阶段自己使用的红色【杀】不能被【闪】响应（全场档；M6 票 17）",
    ),
    # ---- 机制读取方（不是新站点：引擎按层消费符号的那几处）----
    (
        "src/sgs/effects/actions.py",
        "target_is_effective",
        EFFECTIVENESS_TOKEN,
        "ModifierPoint.EFFECTIVENESS,",
    ): Rule(
        MECHANISM,
        1,
        "第 2 层的既有消费口：修饰器链上的有效性查询（不是新的否定站点）",
    ),
    (
        "src/sgs/engine/steps.py",
        "resolve_before_effect",
        UNRESPONSIVE_TOKEN,
        "if has_tag(frame, Tag.UNRESPONSIVE, target=target):",
    ): Rule(
        MECHANISM,
        1,
        "第 4 层：生效前窗口见到『不可响应』就不发无懈询问（既有读取面）",
    ),
    (
        "src/sgs/effects/ops.py",
        "resolve_duel",
        UNRESPONSIVE_TOKEN,
        "if has_tag(frame, Tag.UNRESPONSIVE):",
    ): Rule(
        MECHANISM,
        1,
        "第 4 层：决斗见到**全场档**『不可响应』就不发【杀】响应询问（本票新增的读取面；逐目标档不读）",
    ),
    # ---- 不是四层（同一机制符号，语义相反）----
    (
        "src/sgs/skills/catalog.py",
        "_SKILL_DEFS",
        EFFECTIVENESS_TOKEN,
        "Modifier(ModifierPoint.EFFECTIVENESS, _qinggang_ignore_armor),",
    ): Rule(
        NOT_NEGATION,
        1,
        "【青釭剑】无视防具 = 让对方的『无效』不生效（否定之否定），不是四层里的否定站点",
    ),
}
"""当前全树的否定站点登记（15 处出现 / 13 组）。"""


# ---------------------------------------------------------------------------
# 登记表检查
# ---------------------------------------------------------------------------


def test_the_four_layers_are_exactly_the_d15_closed_set() -> None:
    """四层闭集：新增第五层 / 删掉一层都必须改这里（D15 的裁定点）。"""
    assert set(LAYERS) == {
        "target-creation-cancelled",
        "target-level-invalid",
        "event-frame-invalid",
        "response-window-not-produced",
    }
    assert [layer.status for layer in LAYERS.values()].count(NO_CARRIER) == 1, (
        "『一个无载体』：目标产生前取消（【帷幕】不在本批）"
    )
    assert [layer.status for layer in LAYERS.values()].count(ORDERED_ELSEWHERE) == 0, (
        "票 08 落地【奇才】② 之后，四层里已没有『等别的票』的层"
    )
    assert [layer.status for layer in LAYERS.values()].count(LIVE) == 3, (
        "『一个有载体、一个他票、两个本票』：目标级无效【奋威】+ 响应窗口不产生【离间】"
        "+ 事件帧无效【奇才】②（票 08 起为活站点）"
    )


def test_the_registered_carriers_match_the_catalog_and_the_effort_tickets() -> None:
    """登记的载体与现状一致：四层的载体（含票 08 的【奇才】②）都在 ``CATALOG`` 里。"""
    for key, layer in LAYERS.items():
        for carrier in (() if layer.carrier is None else (layer.carrier,)) + layer.existing_carriers:
            assert carrier in CATALOG, f"{key} 的载体 {carrier} 不在 CATALOG 里"

    assert CATALOG[LAYERS["target-level-invalid"].carrier].skill_type.value == "LIMITED"
    assert CATALOG[LAYERS["response-window-not-produced"].carrier].skill_type.value == "NORMAL"
    assert CATALOG[LAYERS["event-frame-invalid"].carrier].skill_type.value == "NORMAL"


def test_the_registered_behavior_tests_really_exist() -> None:
    """登记表引用的行为测试必须逐字存在（防登记表与测试各说各话）。"""
    defined: set[str] = set()
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text.startswith("def test_") and "(" in text:
                defined.add(text[len("def ") : text.index("(")])
    for key, layer in LAYERS.items():
        for name in layer.tests:
            assert name in defined, f"{key} 的落地测试 {name} 不存在"


def test_every_negation_site_in_the_source_tree_is_registered() -> None:
    """全树审计：未登记 / 计数不符 / 登记失效都红。"""
    problems = audit(scan_tree(), SITES)
    assert not problems, (
        "否定站点与登记表不同步：\n"
        + "\n".join(f"  - {problem}" for problem in problems)
        + "\n\n新增一个否定站点时：先在 LAYERS 里选层（四层闭集），再把它登记进 SITES；"
        "如果它不属于四层，登记为 not-negation 并写清理由。"
    )


def test_the_event_frame_layer_has_its_first_live_site() -> None:
    """『事件帧无效』自票 08 起是**活站点**：定义 / 写入 / 读取三处都在登记表里。

    票 07 的"形状已登记、站点数 = 0"在这里被票 08 的【奇才】② 替换：三处站点逐条可核，
    且全树没有第四处（计数不符会由全树审计报出）。
    """
    event_sites = sorted(
        site for site, _rule in SITES.items() if site[2] == EVENT_FRAME_TOKEN
    )
    assert [site[1] for site in event_sites] == [
        "EVENT_FRAME_INVALID",
        "invalidate_event_frame",
        "take_event_frame_invalid",
    ], "定义 / 写入口 / 读取口各一处"
    assert LAYERS["event-frame-invalid"].status == LIVE


def test_the_four_layers_do_not_share_a_mechanism_symbol() -> None:
    """四层**各自实现、不共用类型**：机制符号两两不交（D15 的"不统一抽象"）。"""
    tokens = {key: set(layer.tokens) for key, layer in LAYERS.items()}
    for left, right in [
        (a, b) for a in tokens for b in tokens if a < b
    ]:
        assert tokens[left].isdisjoint(tokens[right]), (
            f"{left} 与 {right} 共用了机制符号：{tokens[left] & tokens[right]}"
        )


# ---------------------------------------------------------------------------
# 合成探针：证明审计真的会咬
# ---------------------------------------------------------------------------

PROBE_SOURCE = '''\
"""模块 docstring 里的 Tag.UNRESPONSIVE 不算站点（不是 AST 常量）。"""


def probe(frame, target):
    add_tag(frame, Tag.UNRESPONSIVE, target=target)
'''


def test_a_synthetic_unregistered_site_is_reported() -> None:
    """探针①：未登记的新站点必须被报出（且不是靠空转）。"""
    hits = scan_source(PROBE_SOURCE, "src/sgs/probe.py")
    assert sum(hits.values()) == 1, f"探针源码应恰好扫出 1 处：{hits}"
    problems = audit(hits, SITES)
    assert problems, "未登记站点没有被报出——审计空转"
    assert any("未登记" in problem for problem in problems), problems

    site = next(iter(hits))
    assert audit(hits, {site: Rule("response-window-not-produced", 1, "探针：登记")}) == []


def test_a_second_site_in_the_same_function_is_reported() -> None:
    """探针②：同一函数里多一处不加计数也蒙混不过去。"""
    source = PROBE_SOURCE + '''\
    add_tag(frame, Tag.UNRESPONSIVE, target=target)
'''
    hits = scan_source(source, "src/sgs/probe.py")
    site = next(iter(hits))
    assert hits[site] == 2
    problems = audit(hits, {site: Rule("response-window-not-produced", 1, "探针：只登记一处")})
    assert any("站点数变了" in problem for problem in problems), problems


def test_the_event_frame_token_turns_red_the_moment_it_appears() -> None:
    """探针③：票 08 的事件帧符号一旦出现就判红（直到把载体补进登记表）。"""
    source = '''\
"""事件帧无效：票 08 的【奇才】②落地时的形状。"""


def _cancel_the_discard(frame):
    frame.data[EVENT_FRAME_INVALID] = True
'''
    hits = scan_source(source, "src/sgs/skills/probe.py")
    assert sum(hits.values()) == 1, f"探针源码应恰好扫出 1 处：{hits}"
    problems = audit(hits, SITES)
    assert any("未登记" in problem for problem in problems), problems


def test_the_allowlist_itself_does_not_drift_silently() -> None:
    """登记表里的站点必须仍逐字存在（防函数改名后清单变成死账）。"""
    hits = scan_tree()
    for site, rule in SITES.items():
        assert site in hits, (
            f"登记条目在源码里找不到：{site}——函数改名 / 代码搬走时请同步 SITES"
        )
        assert rule.reason.strip(), f"{site} 缺理由"
        assert rule.layer in LAYERS or rule.layer in (MECHANISM, NOT_NEGATION)
