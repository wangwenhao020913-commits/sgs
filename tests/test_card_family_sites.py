"""【杀】家族站点的**全树审计**（M5 retro：族判据只做了一半）。

M5 的 05b / bugfix 两次修的都是同一错型：某处仍在用 ``card.type == "slash"`` 判族，于是
火【杀】/ 雷【杀】/ 转化与虚拟【杀】被漏掉。修一处漏一处的原因是——"哪些地方会决定一张牌
是不是【杀】"从来没有一份可机械核对的清单。

本测试把清单变成门禁：

1. 扫 ``src/sgs`` 里**所有**非 docstring 的字符串字面量 ``"slash"``（比较 / 参数 / 数据表
   的键都算），按"所属函数 / 类 / 模块级常量"归组；
2. 每一个站点都必须在 :data:`ALLOWLIST` 里登记，并二选一：
   - :data:`FAMILY`：判族处（直接或下游调用 ``is_slash`` / ``satisfies_required``）；
   - :data:`NORMAL`：**有意**只认普通【杀】（如朱雀羽扇的源筛选、藤甲免疫、转化产物），
     每条必须写一行理由；
3. 新站点（含"同一函数里多出第二处"）⇒ 红；allowlist 里的条目消失（函数改名 / 代码搬走）
   ⇒ 也红——清单与源码必须同步；
4. 合成探针证明审计真的会咬：未登记的 ``card.type == "slash"`` 被报出，已登记的同一片段
   不报，函数里多一处同片段时报计数不符。

判据只读 AST + 源码文本：不 import ``src/sgs``（否则扫描对象会被运行时副作用污染）。
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "sgs"

FAMILY = "family-aware"
NORMAL = "normal-only"

TARGET = "slash"
"""家族键：与 :data:`sgs.effects.behaviors.SLASH_FAMILY_KEY` 同一个线上取值。"""


@dataclass(frozen=True)
class Rule:
    """一条登记：``count`` = 该片段在同一所属处出现的次数（防"同函数多一处"漏网）。"""

    kind: str
    count: int
    reason: str


Site = tuple[str, str, str]
"""``(相对路径, 所属函数 / 类 / 模块级常量, 归一化源码行)``。"""


# ---------------------------------------------------------------------------
# 扫描器
# ---------------------------------------------------------------------------


def _docstring_constants(tree: ast.AST) -> set[int]:
    """模块 / 类 / 函数的 docstring 节点 id——docstring 里的提及**不算**落点。"""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            out.add(id(body[0].value))
    return out


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _owner(node: ast.AST, tree: ast.AST) -> str:
    """站点所属的**稳定名字**：函数 / 类的点分名；模块级取最近的赋值目标名。

    模块级常量（``_VINE_ARMOR_IMMUNE_CARD_TYPES`` / ``CARD_BEHAVIORS`` / ``GREEN_DRAGON_FLOW``
    / ``CONVERSION_SKILLS``……）是"族判据"最常见的住址；若统一记成 ``<module>``，同一文件里
    新增一个模块级站点就会被已有的 ``<module>`` 条目遮住。故模块级下沉到赋值目标名。
    """
    parent = _parent_map(tree)
    parts: list[str] = []
    cursor: ast.AST = node
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
        if isinstance(holder, ast.Assign):
            target = holder.targets[0]
            if isinstance(target, ast.Name):
                return target.id
        if isinstance(holder, ast.AnnAssign):
            target = holder.target
            if isinstance(target, ast.Name):
                return target.id
        cursor = holder
    return "<module>"


def scan_source(source: str, path: str) -> Counter[Site]:
    """扫一份源码，返回 ``{(路径, 所属, 片段): 次数}``。"""
    tree = ast.parse(source)
    docstrings = _docstring_constants(tree)
    lines = source.splitlines()
    hits: Counter[Site] = Counter()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Constant)
            and node.value == TARGET
            and id(node) not in docstrings
        ):
            continue
        snippet = " ".join(lines[node.lineno - 1].split())
        hits[(path, _owner(node, tree), snippet)] += 1
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


def audit(hits: Counter[Site], allowlist: dict[Site, Rule]) -> list[str]:
    """把扫描结果与登记表对照；返回**全部**问题（不 fail-fast，一次看全）。"""
    problems: list[str] = []
    for site, count in sorted(hits.items()):
        rule = allowlist.get(site)
        label = f"{site[0]} :: {site[1]} :: {site[2]}"
        if rule is None:
            problems.append(
                f"未分类的 【杀】族站点：{label}（出现 {count} 次）——"
                f"登记为 {FAMILY}（判族走 is_slash / satisfies_required）或 {NORMAL}（写一行理由）"
            )
        elif rule.kind not in (FAMILY, NORMAL):
            problems.append(f"{label} 的分类 {rule.kind!r} 不是 {FAMILY!r} / {NORMAL!r}")
        elif not rule.reason.strip():
            problems.append(f"{label} 缺少一行理由")
        elif rule.count != count:
            problems.append(
                f"{label} 的站点数变了：登记 {rule.count} 处，实得 {count} 处——"
                "这多半是同一处又加了一行判族代码（或删了一处）"
            )
    for site in sorted(set(allowlist) - set(hits)):
        problems.append(
            f"allowlist 条目已不存在：{site[0]} :: {site[1]} :: {site[2]}——"
            "函数改名 / 代码搬走时请同步这一份清单"
        )
    return problems


# ---------------------------------------------------------------------------
# 登记表：当前全树 28 处 / 26 组（M5 05b + bugfix 之后的真实状态）
# ---------------------------------------------------------------------------

ALLOWLIST: dict[Site, Rule] = {
    # ---- agents/heuristic.py：估值与藤甲建模 ----
    (
        "src/sgs/agents/heuristic.py",
        "_CARD_VALUES",
        '"slash": 3.0,',
    ): Rule(FAMILY, 1, "杀族兜底价：_card_value 对火 / 雷 / 转化杀读这一档（_is_slash_card）"),
    (
        "src/sgs/agents/heuristic.py",
        "_card_value",
        'return _CARD_VALUES["slash"]',
    ): Rule(FAMILY, 1, "族判据已知是【杀】后回落到族价，不逐牌名抄表"),
    (
        "src/sgs/agents/heuristic.py",
        "_damage_to",
        'if card.type == "slash":',
    ): Rule(NORMAL, 1, "藤甲只免疫**普通**杀（火杀 +1 / 雷杀照常）——有意 normal-only，与 _VINE_ARMOR_IMMUNE 同口径"),
    (
        "src/sgs/agents/heuristic.py",
        "_DECK_TABLE",
        '"slash": 30,',
    ): Rule(NORMAL, 3, "记牌的牌堆档位表按**牌名**逐项记数：'slash' 就是普通【杀】，火 / 雷各有自己的一行（三档各一处，不是判族站点）"),
    (
        "src/sgs/agents/heuristic.py",
        "_POOL_SLASH_TYPES",
        '_POOL_SLASH_TYPES = frozenset({"slash", "fire_slash", "thunder_slash"})',
    ): Rule(
        FAMILY,
        1,
        "C2（票 23）的未知牌期望把档位表的**牌名**折成族价：这一行就是族登记表"
        "（火 / 雷杀与普通杀同价），新加第五条【杀】时它与 _DECK_TABLE 的反查一起红",
    ),
    (
        "src/sgs/agents/heuristic.py",
        "_pool_card_value",
        'return _CARD_VALUES["slash"]',
    ): Rule(FAMILY, 1, "档位表牌名经族登记判定是【杀】后回落到族价（与 _card_value 同一口径）"),
    # ---- effects/actions.py：响应候选的语境分派 ----
    (
        "src/sgs/effects/actions.py",
        "_respond",
        'if ctx.required in ("slash", "dodge"):',
    ): Rule(FAMILY, 1, "required 是**请求牌名**；成员判定在下游 satisfies_required，这里只决定追加哪趟转化候选"),
    (
        "src/sgs/effects/actions.py",
        "_respond",
        'scope = CandidateScope.RESPOND if ctx.required == "slash" else CandidateScope.DODGE',
    ): Rule(FAMILY, 1, "同上：按请求牌名选 CandidateScope，不筛具体牌面"),
    # ---- effects/behaviors.py：杀族计数键与行为表 ----
    (
        "src/sgs/effects/behaviors.py",
        "SLASH_FAMILY_KEY",
        'SLASH_FAMILY_KEY = "slash"',
    ): Rule(FAMILY, 1, "杀族共享计数键的**定义处**；is_slash 只读这个键"),
    (
        "src/sgs/effects/behaviors.py",
        "CARD_BEHAVIORS",
        '"slash": CardBehavior(',
    ): Rule(FAMILY, 1, "普通杀的行为条目：同一块的 count_key='slash' 使它加入家族（族登记处）"),
    (
        "src/sgs/effects/behaviors.py",
        "CARD_BEHAVIORS",
        'card_type_id="slash",',
    ): Rule(FAMILY, 1, "普通杀条目的牌型 id；族归属由同一块的 count_key 声明，不是靠这个 id 认族"),
    (
        "src/sgs/effects/behaviors.py",
        "_slash_variant",
        'rated=1, extra=0, counts=True, ignore_distance=False, count_key="slash"',
    ): Rule(FAMILY, 1, "火 / 雷杀条目声明同一计数键 ⇒ 与普通杀共享使用次数（家族成员）"),
    (
        "src/sgs/effects/behaviors.py",
        "CARD_BEHAVIORS",
        'rated=1, extra=0, counts=True, ignore_distance=False, count_key="slash"',
    ): Rule(FAMILY, 1, "普通杀条目的计数键声明（与 _slash_variant 同一个键）"),
    # ---- effects/ops.py：响应请求（成员判定走 satisfies_required）----
    (
        "src/sgs/effects/ops.py",
        "resolve_duel",
        'required="slash",',
    ): Rule(FAMILY, 1, "决斗的响应请求是杀族请求；ask_respond 的候选成员判定走 satisfies_required"),
    (
        "src/sgs/effects/ops.py",
        "resolve_borrowed_sword",
        'required="slash",',
    ): Rule(FAMILY, 2, "借刀两处请求（offers 预检 / 真询问）都是杀族请求，成员判定同 satisfies_required"),
    (
        "src/sgs/effects/ops.py",
        "resolve_barbarian_invasion",
        'required="slash",',
    ): Rule(FAMILY, 1, "南蛮的响应请求是杀族请求；成员判定走 satisfies_required"),
    # ---- skills/catalog.py ----
    (
        "src/sgs/skills/catalog.py",
        "_VINE_ARMOR_IMMUNE_CARD_TYPES",
        '_VINE_ARMOR_IMMUNE_CARD_TYPES = frozenset({"slash", "barbarian_invasion", "arrow_barrage"})',
    ): Rule(NORMAL, 1, "藤甲只免疫普通杀（火 / 雷按属性另行结算）——有意 normal-only，方向与族判据相反"),
    (
        "src/sgs/skills/catalog.py",
        "_normal_slash",
        'return state.card(card_id).type == "slash"',
    ): Rule(NORMAL, 1, "朱雀羽扇的**触发条件**（实体牌侧）：只收普通杀，避免产物属性按来源递归漂移（题面点名的 normal-only）"),
    (
        "src/sgs/skills/catalog.py",
        "_fan_is_normal_slash",
        'return card.type == "slash"',
    ): Rule(NORMAL, 1, "朱雀羽扇的触发条件（虚拟 / 转化牌侧）：产物牌型必须是普通【杀】"),
    (
        "src/sgs/skills/catalog.py",
        "_SKILL_DEFS",
        'virtual_type_ids=("slash",),',
    ): Rule(NORMAL, 1, "丈八蛇矛的转化**产物**是普通杀牌型——有意产出普通 target_type_id"),
    (
        "src/sgs/skills/catalog.py",
        "_green_dragon_plan",
        'provide_face(state, instance.owner, "slash", (Zone.HAND,))[0]',
    ): Rule(FAMILY, 1, "provide_face 用 satisfies_required 筛面 ⇒ 05b-fix 后含火 / 雷 / 转化杀"),
    (
        "src/sgs/skills/catalog.py",
        "_serpent_spear_candidate",
        'target_type_id="slash",',
    ): Rule(NORMAL, 1, "转化候选的**产物**是普通杀牌型（与虚拟 id 段一致），源两张手牌不按族筛"),
    (
        "src/sgs/skills/catalog.py",
        "GREEN_DRAGON_FLOW",
        'type_id="slash",',
    ): Rule(FAMILY, 1, "PickProvide 的面同样经 satisfies_required 过滤（与 _green_dragon_plan 同源）"),
    # ---- skills/heroes.py：转化产物 ----
    # ---- skills/active.py：界刘备【仁德】的"视为使用一张基本牌"（M6 票 16）----
    (
        "src/sgs/skills/active.py",
        "_JIE_RENDE_BASIC_TYPES",
        'JIE_RENDE_BASIC_SLASH: "slash",',
    ): Rule(NORMAL, 1, "界【仁德】视为使用的【杀】**产物**是普通杀牌型——有意 normal-only"),
    (
        "src/sgs/skills/active.py",
        "_jie_rende_effect",
        'if type_id == "slash":',
    ): Rule(NORMAL, 1, "按产物牌型决定要不要选目标（普通杀需要目标；桃/酒只对自己）"),
    # ---- skills/heroes.py：转化产物 ----
    (
        "src/sgs/skills/heroes.py",
        "CONVERSION_SKILLS",
        'target_type_id="slash",',
    ): Rule(
        NORMAL,
        4,
        "武圣（标准 / 界）/ 龙胆 / 神关羽【武神】的转化**产物**是普通杀牌型；源谓词才用 is_slash（方向相反）",
    ),
    # ---- skills/lord_request.py ----
    (
        "src/sgs/skills/lord_request.py",
        "JIJIANG",
        'required="slash",',
    ): Rule(FAMILY, 1, "激将的请求牌名是杀族；候选 / 响应成员判定走 satisfies_required"),
    (
        "src/sgs/skills/lord_request.py",
        "_lord_request_definition",
        'requirement = {"dodge": "打出【闪】", "slash": "打出【杀】"}[skill.required]',
    ): Rule(FAMILY, 1, "按 required 取文案（请求牌名，不筛牌面；护驾 / 激将各一档）"),
    # ---- skills/lord_request.py：界【激将】①②（M6 票 16）----
    (
        "src/sgs/skills/lord_request.py",
        "JIE_JIJIANG_LORD",
        'required="slash",',
    ): Rule(FAMILY, 1, "界【激将】①的请求牌名是杀族；候选 / 响应成员判定走 satisfies_required"),
    (
        "src/sgs/skills/lord_request.py",
        "JIE_JIJIANG_ECHO",
        'card_type="slash",',
    ): Rule(FAMILY, 1, "界【激将】②的牌面判据走 satisfies_required（火 / 雷 / 转化杀同族）"),
    (
        "src/sgs/skills/lord_request.py",
        "_lord_echo_requirement",
        '"slash": "当你需要使用或打出【杀】时，你可以令其他蜀势力角色依次选择是否打出一张【杀】",',
    ): Rule(FAMILY, 1, "按 required 取 ① 的文案（请求牌名，不筛牌面）"),
    # ---- skills/response_window.py / target_pool.py：声明闭集 ----
    (
        "src/sgs/skills/response_window.py",
        "WUSHUANG",
        'card_types=("slash", "duel"),',
    ): Rule(FAMILY, 1, "声明闭集按 satisfies_required 逐项匹配（05b-fix：火 / 雷杀也求两张）"),
    (
        "src/sgs/skills/response_window.py",
        "ResponseCountSkill.response_count",
        'if "slash" in self.card_types and satisfies_required(ctx.card, "slash"):',
    ): Rule(
        FAMILY,
        2,
        "票 23 把牌名闭集拆成【杀】/【决斗】两条分支（两半改的是不同的一方）；"
        "这一行本身出现两个字面量（成员测试 + 判族实参），故计数为 2；"
        "【杀】这一支仍走 satisfies_required ⇒ 火 / 雷 / 转化杀同族",
    ),
    (
        "src/sgs/skills/target_pool.py",
        "TARGET_POOL_SKILLS",
        'card_types=("slash", "duel"),',
    ): Rule(
        FAMILY,
        2,
        "空城（标准 / 界）的牌名闭集同样走 satisfies_required（火 / 雷杀也进不了目标池）",
    ),
    # ---- skills/awakening.py：界徐庶【诛害】（M6 票 06） ----
    (
        "src/sgs/skills/awakening.py",
        "ZHUHAI_FLOW",
        'card_types=("slash",),',
    ): Rule(FAMILY, 1, "【诛害】可用的【杀】按**家族**判（PickCards 的 card_types 走 satisfies_required：火 / 雷 / 转化杀同族）"),
    (
        "src/sgs/skills/awakening.py",
        "_zhuhai_illegal_cards",
        'if not satisfies_required(card, "slash"):',
    ): Rule(FAMILY, 1, "距离 / 次数闸门逐张判「是不是【杀】」同样按家族（火 / 雷 / 转化杀与普通杀同一条使用规则）"),
    (
        "src/sgs/skills/awakening.py",
        "_zhuhai_plan",
        'and satisfies_required(state.card(card_id), "slash")',
    ): Rule(FAMILY, 1, "available 判据与选择面同源（family-aware，避免「计划问、步骤却付不起」）"),
    # ---- skills/virtual.py：虚拟牌命名空间与产物 ----
    (
        "src/sgs/skills/virtual.py",
        "_KIND_BY_TYPE",
        '"slash": VirtualKind.SLASH,',
    ): Rule(NORMAL, 1, "虚拟牌命名空间按**产物牌名**登记普通杀段，不是族判据（火杀另有 FIRE_SLASH 段）"),
    (
        "src/sgs/skills/virtual.py",
        "transformed_slash",
        'return transformed_card(state, sources, "slash")',
    ): Rule(NORMAL, 1, "丈八的**产物**是普通杀牌型（有意 normal-only 产物）"),
}


def test_every_slash_literal_in_the_source_tree_is_classified() -> None:
    """全树审计：未登记 / 计数不符 / 登记失效都红。"""
    problems = audit(scan_tree(), ALLOWLIST)
    assert not problems, (
        "【杀】家族站点与登记表不同步：\n"
        + "\n".join(f"  - {problem}" for problem in problems)
        + "\n\n新增站点时：先判断它是不是**家族判据**——是 ⇒ 改调 "
        "`is_slash` / `satisfies_required` 并登记为 family-aware；确实只认普通【杀】"
        "（源筛选 / 产物 / 免疫）⇒ 登记为 normal-only 并写一行理由。"
    )


# ---------------------------------------------------------------------------
# 合成探针：证明审计会咬
# ---------------------------------------------------------------------------

PROBE_SOURCE = '''\
"""模块 docstring 里的 "slash" 不算站点。"""


def probe(card):
    return card.type == "slash"
'''


def test_a_synthetic_unclassified_slash_site_is_reported() -> None:
    """探针①：未登记的 ``card.type == "slash"`` 必须被报出（且不是靠空转）。"""
    hits = scan_source(PROBE_SOURCE, "src/sgs/probe.py")
    assert sum(hits.values()) == 1, f"探针源码应恰好扫出 1 处：{hits}"
    problems = audit(hits, ALLOWLIST)
    assert problems, "未登记站点没有被报出——审计空转"
    assert any("未分类" in problem for problem in problems), problems

    # 反面自检：把同一站点登记进临时表后必须变绿（否则"红"可能只是格式错）。
    site = next(iter(hits))
    assert audit(hits, {site: Rule(FAMILY, 1, "探针：判族")}) == []


def test_a_second_slash_site_in_the_same_function_is_reported() -> None:
    """探针②：同一函数里**多一处**同片段不能靠"所属同名"蒙混——计数要变红。"""
    source = PROBE_SOURCE + '''\
def probe(card, other):
    return card.type == "slash"
'''
    hits = scan_source(source, "src/sgs/probe.py")
    site = next(iter(hits))
    assert hits[site] == 2
    problems = audit(hits, {site: Rule(FAMILY, 1, "探针：只登记一处")})
    assert any("站点数变了" in problem for problem in problems), problems


def test_the_allowlist_itself_does_not_drift_silently() -> None:
    """allowlist 里的站点必须仍逐字存在（防函数改名后清单变成死账）。"""
    hits = scan_tree()
    for site, rule in ALLOWLIST.items():
        assert site in hits, (
            f"登记条目在源码里找不到：{site}——函数改名 / 代码搬走时请同步 allowlist"
        )
        assert rule.reason.strip(), f"{site} 缺理由"
        assert rule.kind in (FAMILY, NORMAL)


# ---------------------------------------------------------------------------
# M5 两次族修复的**回归锚点**：这些站点必须按此分类存在
# ---------------------------------------------------------------------------

M5_FIX_ANCHORS: tuple[tuple[str, str, str], ...] = (
    # 05b（3c1a399）：青龙的"提供一张【杀】"面走 satisfies_required，含火 / 雷 / 转化杀。
    ("src/sgs/skills/catalog.py", "_green_dragon_plan", FAMILY),
    # 05b：青龙流程的 PickProvide 面与上面的判据同源。
    ("src/sgs/skills/catalog.py", "GREEN_DRAGON_FLOW", FAMILY),
    # 05b：无双 / 空城的牌名闭集逐项走 satisfies_required。
    ("src/sgs/skills/response_window.py", "WUSHUANG", FAMILY),
    ("src/sgs/skills/target_pool.py", "TARGET_POOL_SKILLS", FAMILY),
    # 05b：朱雀羽扇的**源筛选**有意只收普通【杀】（产物属性不递归漂移）。
    ("src/sgs/skills/catalog.py", "_normal_slash", NORMAL),
    # 票 09：询问式触发后，虚拟 / 转化牌侧同样只认普通【杀】。
    ("src/sgs/skills/catalog.py", "_fan_is_normal_slash", NORMAL),
    # 藤甲只免疫普通【杀】——有意 normal-only。
    ("src/sgs/skills/catalog.py", "_VINE_ARMOR_IMMUNE_CARD_TYPES", NORMAL),
    # 转化**产物**有意是普通【杀】牌型（武圣 / 龙胆 / 丈八 / 蛇矛）。
    ("src/sgs/skills/heroes.py", "CONVERSION_SKILLS", NORMAL),
    ("src/sgs/skills/catalog.py", "_serpent_spear_candidate", NORMAL),
    ("src/sgs/skills/catalog.py", "_SKILL_DEFS", NORMAL),
    ("src/sgs/skills/virtual.py", "transformed_slash", NORMAL),
)


def test_the_two_m5_slash_fixes_are_classified() -> None:
    """05b（3c1a399）与 bugfix（846eeeb）涉及的站点全部在册、分类正确。

    846eeeb 把雌雄双股剑的 ``card.type == "slash"`` 换成 ``is_slash(...)``——那处**不再含**
    字面量，故审计天然看不到；它的"家族化"由下面这条"普通杀比较只剩有意站点"的断言兜住。
    """
    for path, owner, expected in M5_FIX_ANCHORS:
        matching = [
            (site, rule)
            for site, rule in ALLOWLIST.items()
            if site[0] == path and site[1] == owner
        ]
        assert matching, f"M5 修复站点未登记：{path} :: {owner}"
        assert any(rule.kind == expected for _, rule in matching), (
            f"{path} :: {owner} 的分类应为 {expected}，实得 "
            f"{[rule.kind for _, rule in matching]}"
        )


def test_no_comparison_against_the_bare_slash_literal_remains_unclassified() -> None:
    """所有 ``<expr>.type == / != "slash"`` 的比较只剩两处**有意**的普通杀判据。

    05b 之后仍保留牌型相等比较的地方只有：朱雀羽扇的源筛选（``_normal_slash``）与
    AI 对藤甲的普通【杀】免疫建模（``_damage_to``）。其余任何新的 ``.type == "slash"``
    都应改用 ``is_slash``——这条断言把"审计只看字面量、不看比较形态"的缝补上。
    """
    hits = scan_tree()
    comparisons = {
        (site, count)
        for site, count in hits.items()
        if ".type ==" in site[2] or ".type !=" in site[2]
    }
    assert comparisons, "全树扫不到任何牌型比较——扫描器失效"
    owners = {site[1] for site, _ in comparisons}
    assert owners == {"_normal_slash", "_fan_is_normal_slash", "_damage_to"}, (
        "牌型相等比较只许出现在朱雀羽扇的源筛选与藤甲免疫建模里，实得："
        f"{sorted(owners)}"
    )
