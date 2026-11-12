"""**要牌窗口**的全树审计（E2 票 16：闭集 + 登记表 + 机械闸）。

用户的裁定（2026-11-09 逐字）：「任何"需要"、"可以"使用或打出的场合，例如贾诩乱武也算
（把这个"需要"、"可以"使用或打出给规范下来，转化计才能真正排除bug，不然每新加一个技能
都可能产生bug）」。本文件把这句话变成门禁：

1. 扫 ``src/sgs`` 里**所有**能"要一张牌"的站点，按"所属函数 / 类 / 模块级常量"归组：
   - ``dispatch``：构造 ``ModifierContext(..., respond_required=...)``（候选派发 / 窗口事实
     的透传）；
   - ``declaration``：``LordSkill(required=..., scope=...)``（势力求助窗口的声明）；
   - ``ask`` / ``preview``：``respond_context`` 建的窗口载荷（所在函数还发问 ⇒ ``ask``，只
     预览 ⇒ ``preview``）/ ``respond_candidates``（不耗序号的预览；它只消费别人建好的载荷）；
   - ``askkind``：``ask_query(QueryKind.DODGE | PEACH, ...)``（【闪】步 / 濒死求【桃】）；
   - ``accept``：``start_provider(..., required=...)``（受理面把"要哪张牌"交给提供者）；
2. 每一个站点都必须在 :data:`sgs.skills.catalog.CARD_REQUEST_WINDOWS` 里占一行；未登记 ⇒ 红，
   登记表里有、源码里没有 ⇒ 也红（防函数改名后登记表变成死账）；
3. **闭集覆盖**：``WindowCause`` 的每个成员都要有一行窗口认领它——新加一类要牌窗口（新成因）
   ⇒ 红，逼出"这扇窗口要不要让转化 / 提供者参与"的一次裁定；
4. 合成探针证明审计真的会咬：未登记的新窗口被报出、同一函数里多一处同形派发计数变红。

**只读 AST + 源码文本**（与 ``tests/test_card_family_sites.py`` / ``test_negation_sites.py``
同一姿态），不 import ``src/sgs`` 来扫描——扫描对象不会被运行时副作用污染。登记表本身从
``sgs.skills.catalog`` import（它是**代码侧唯一真源**，不存在第二份清单）。
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "sgs"
if str(REPO_ROOT / "src") not in sys.path:  # 与多数测试同一条引导（单独跑时也能 import）
    sys.path.insert(0, str(REPO_ROOT / "src"))

from sgs.data import load_content_pack  # noqa: E402
from sgs.effects import CandidateScope, ModifierContext, legal_actions_for  # noqa: E402
from sgs.skills.catalog import (  # noqa: E402
    CARD_REQUEST_SCOPES,
    CARD_REQUEST_WINDOWS,
    REQUEST_PLUMBING,
    REQUEST_WINDOW,
    ProviderWindows,
    RequestAxis,
    required_card,
)
from sgs.state import (  # noqa: E402
    ActivateSkill,
    QueryKind,
    RespondContext,
    RespondMode,
    WindowCause,
)

from support import SeatSpec, build_state, cards_of_type, deck_excluding  # noqa: E402

Site = tuple[str, str, str, str]
"""``(kind, 相对路径, 所属函数 / 类 / 模块级常量, AST 指纹)``。

指纹由扫描器从语法树现算（``ast.unparse`` 的关键字实参 + 缺省标记），故"同一函数里把
``scope=`` 改掉 / 把 ``required=`` 换成另一个常量"也会让登记条目对不上——登记表与源码必须
逐字同步，而不是"函数名还在就算"。
"""

GUOHUO = "hero.yuji_mobile.guohuo"
"""【蛊惑】的全限定 id（本票是它唯一的生产调用者）。"""


# ---------------------------------------------------------------------------
# 扫描器
# ---------------------------------------------------------------------------


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _owner(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    """站点所属的**稳定名字**：函数 / 类的点分名；模块级取最近的赋值目标名。

    与 ``tests/test_card_family_sites.py:_owner`` 同一套读法：模块级常量（``HUJIA`` /
    ``JIE_JIJIANG_LORD`` 一类声明）必须落到常量名上，否则同一文件里新增一条声明会被已有的
    ``<module>`` 条目遮住。
    """
    parts: list[str] = []
    cursor: ast.AST = node
    while cursor in parents:
        holder = parents[cursor]
        if isinstance(holder, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(holder.name)
        cursor = holder
    if parts:
        return ".".join(reversed(parts))
    cursor = node
    while cursor in parents:
        holder = parents[cursor]
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


def _callee(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _keyword_text(keywords: dict[str, ast.expr], name: str) -> str:
    """关键字的**源码文本**（缺省 ⇒ ``<default>``，出现即进指纹）。"""
    if name not in keywords:
        return "<default>"
    return ast.unparse(keywords[name])


def _site_of(call: ast.Call, keywords: dict[str, ast.expr]) -> tuple[str, str] | None:
    """``(kind, 指纹)``；不是要牌 / 受理站点 ⇒ ``None``。

    六类标记（见模块 docstring）之外一律不算：改变的是"要不要一张牌"这件事，而不是所有
    提到 ``respond_required`` 的代码（源码里的**读者**不登记——它们是窗口事实的使用方，
    不是窗口本身）。
    """
    name = _callee(call)
    if name == "ModifierContext" and "respond_required" in keywords:
        return "dispatch", (
            f"scope={_keyword_text(keywords, 'scope')}"
            f" required={_keyword_text(keywords, 'respond_required')}"
        )
    if name == "LordSkill" and "required" in keywords and "scope" in keywords:
        return "declaration", (
            f"required={_keyword_text(keywords, 'required')}"
            f" scope={_keyword_text(keywords, 'scope')}"
            # `respond_modes`（票 22）是**一档集合**：说出这条声明覆盖使用 / 打出哪几档。
            # 不写它 ⇒ `<default>`（不按 mode 筛）；改值 / 改名都会让登记行对不上。
            f" mode={_keyword_text(keywords, 'respond_modes')}"
        )
    if name in ("respond_context", "respond_candidates"):
        # 响应窗口的**声明点** = `respond_context`（预筛口 / 预览 / 真询问只收它建的那一份
        # 载荷，票 02 的唯一形状）；`respond_candidates` 只是**消费**这份载荷的预览口，故
        # 它没有窗口关键字（三个都 `<default>`）——窗口事实不再由预览这一侧重复声明。
        return ("context" if name == "respond_context" else "preview"), (
            f"cause={_keyword_text(keywords, 'cause')}"
            f" required={_keyword_text(keywords, 'required')}"
            f" mode={_keyword_text(keywords, 'mode')}"
        )
    if name == "start_provider":
        return "accept", f"required={_keyword_text(keywords, 'required')}"
    if name == "ask_query" and call.args:
        kind = ast.unparse(call.args[0])
        if kind in ("QueryKind.DODGE", "QueryKind.PEACH"):
            payload = next((key.arg for key in call.keywords if key.arg == "payload"), "<none>")
            return "askkind", f"kind={kind} payload={payload}"
    return None


def scan_source(source: str, path: str) -> Counter[Site]:
    """扫一份源码，返回 ``{(kind, 路径, 所属, 指纹): 次数}``。

    ``respond_context`` 的类别按**所在函数有没有真发问**分：同一函数里还调了
    ``ask_respond`` ⇒ ``ask``；只有预览（例如乱武的"最近者里谁有可用【杀】"）⇒ ``preview``。
    """
    tree = ast.parse(source)
    parents = _parent_map(tree)
    asking = {
        _owner(node, parents)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node) == "ask_respond"
    }
    hits: Counter[Site] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {key.arg: key.value for key in node.keywords if key.arg is not None}
        classified = _site_of(node, keywords)
        if classified is None:
            continue
        kind, fingerprint = classified
        owner = _owner(node, parents)
        if kind == "context":
            kind = "ask" if owner in asking else "preview"
        hits[(kind, path, owner, fingerprint)] += 1
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


# ---------------------------------------------------------------------------
# 登记表 ↔ 源码的对照
# ---------------------------------------------------------------------------


def registered_sites(rows=CARD_REQUEST_WINDOWS) -> tuple[dict[Site, int], list[str]]:
    """把登记表折成 ``{站点: 次数}``；顺带报**登记表自身**的问题。

    同一站点可以被多行认领（``actions._respond`` 的一趟派发同时服务南蛮 / 决斗 / 借刀 /
    乱武 / 无懈五扇窗口）——认领可以重复，**次数必须一致**，否则"这处到底有几条派发"就会
    出现两个答案。
    """
    registered: dict[Site, int] = {}
    problems: list[str] = []
    for row in rows:
        for site in row.sites:
            key = (site.kind, site.path, site.owner, site.fingerprint)
            previous = registered.get(key)
            if previous is not None and previous != site.count:
                problems.append(
                    f"站点 {key[1]} :: {key[2]} 被多个窗口登记成了不同次数"
                    f"（{previous} vs {site.count}）"
                )
            registered[key] = max(site.count, previous or 0)
    return registered, problems


def unregistered(hits: Counter[Site], known: dict[Site, int]) -> list[str]:
    """源码里有、登记表里没有的站点。"""
    problems: list[str] = []
    for site, count in sorted(hits.items()):
        if site not in known:
            problems.append(f"未登记的要牌站点：{' :: '.join(site)}（出现 {count} 次）")
    return problems


def stale(hits: Counter[Site], known: dict[Site, int]) -> list[str]:
    """登记表里有、源码里没有（或次数变了）的条目。"""
    problems: list[str] = []
    for site, count in sorted(known.items()):
        actual = hits.get(site)
        if actual is None:
            problems.append(
                f"登记表条目在源码里找不到：{' :: '.join(site)}——函数改名 / 代码搬走 / "
                "指纹（scope / required / cause）改了都要同步这一行"
            )
        elif actual != count:
            problems.append(
                f"站点数变了：{' :: '.join(site)} 登记 {count} 处，实得 {actual} 处——"
                "同一函数里又多了一处同形派发，或删掉了一处"
            )
    return problems


# ---------------------------------------------------------------------------
# 断言 1 / 2：站点全覆盖 + 登记表不陈旧
# ---------------------------------------------------------------------------


def test_every_card_request_site_is_registered() -> None:
    """全树审计：任何"要一张牌"的站点都必须登记（新技能引入新窗口 ⇒ 红）。"""
    hits = scan_tree()
    known, table_problems = registered_sites()
    problems = [*table_problems, *unregistered(hits, known)]
    assert not problems, (
        "要牌站点与登记表（sgs.skills.catalog.CARD_REQUEST_WINDOWS）不同步：\n"
        + "\n".join(f"  - {problem}" for problem in problems)
        + "\n\n新加一处要牌窗口时：先裁「要不要让转化 / 提供者参与」与「需要还是可以」"
        "（RequestAxis），再在 CARD_REQUEST_WINDOWS 里加一行；照 "
        "docs/agents/skill-authoring/shapes.md §3.8 的步骤登记。"
    )


def test_the_registry_has_no_stale_rows() -> None:
    """登记表里的站点 / 次数必须仍逐字存在；行本身的形状（key / 理由 / 参与面）也要成立。"""
    hits = scan_tree()
    known, table_problems = registered_sites()
    problems = [*table_problems, *stale(hits, known)]

    keys = [row.key for row in CARD_REQUEST_WINDOWS]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicated, f"登记表里有重复的 key：{duplicated}"
    for row in CARD_REQUEST_WINDOWS:
        label = f"{row.kind}:{row.key}"
        assert row.reason.strip(), f"{label} 缺少一行理由"
        assert row.sites, f"{label} 没有认领任何站点"
        if row.kind == REQUEST_WINDOW:
            assert row.label.strip(), f"{label} 缺少一句窗口说明"
            assert row.scope is None or isinstance(row.scope, CandidateScope), label
            assert all(isinstance(cause, WindowCause) for cause in row.causes), label
            assert all(isinstance(mode, RespondMode) for mode in row.modes), label
            assert row.axis is None or isinstance(row.axis, RequestAxis), label
        else:
            assert row.kind == REQUEST_PLUMBING, f"{label} 的 kind 只许是 {REQUEST_WINDOW!r} / {REQUEST_PLUMBING!r}"
    assert not problems, (
        "登记表里有陈旧条目：\n" + "\n".join(f"  - {problem}" for problem in problems)
    )


# ---------------------------------------------------------------------------
# 断言 2′：窗口成因是**闭集**（新窗口 ⇒ 必须裁参与的机制）
# ---------------------------------------------------------------------------


def test_every_window_cause_has_a_registered_window() -> None:
    """``WindowCause`` 的每个成员都要有一行窗口认领。

    新加一个成因 = 引擎里多了一扇"要一张牌"的窗口；没有登记行就没有人回答"这扇窗口要不
    要让转化 / 提供者参与、是'需要'还是'可以'"——本票要防的正是这种静默跳过。
    """
    claimed = {
        cause for row in CARD_REQUEST_WINDOWS if row.kind == REQUEST_WINDOW for cause in row.causes
    }
    missing = sorted(set(WindowCause) - claimed, key=lambda cause: cause.value)
    assert not missing, (
        "这些窗口成因没有任何登记行："
        f"{[cause.value for cause in missing]}\n"
        "新加一扇要牌窗口时，在 CARD_REQUEST_WINDOWS 里加一行（scope / mode / 要的牌 / "
        "需要还是可以 / 谁可以参与）。"
    )


def test_the_request_scopes_are_exactly_the_non_play_scopes() -> None:
    """共享判据只认"要牌语境"：``PLAY``（出牌阶段）与 ``CARD``（逐张牌）都不是要牌窗口。"""
    assert CandidateScope.PLAY not in CARD_REQUEST_SCOPES
    assert CandidateScope.CARD not in CARD_REQUEST_SCOPES
    assert CARD_REQUEST_SCOPES == {
        CandidateScope.DODGE,
        CandidateScope.RESPOND,
        CandidateScope.PEACH,
        CandidateScope.NULLIFY,
    }, (
        "要牌语境的闭集变了：provider 分支 / 转化技的接入面都按这份集合推导"
        "（sgs.skills.catalog.CARD_REQUEST_SCOPES）"
    )


# ---------------------------------------------------------------------------
# 断言 3：合成反例（证明闸不是空转）
# ---------------------------------------------------------------------------

PROBE_SOURCE = '''\
"""模块 docstring 里提到 respond_required 不算站点。"""


def probe(state):
    return apply_modifiers(
        ModifierPoint.CANDIDATES,
        state,
        ModifierContext(subject=0, scope=CandidateScope.DODGE, respond_required="dodge"),
        (),
    )


PROBE_LORD = LordSkill(
    hero_id="probe", short_id="probe", required="dodge", scope=CandidateScope.DODGE
)


def probe_window(host, frame, seat):
    ctx = host.respond_context(frame, seat, cause=WindowCause.DUEL, required="slash")
    return host.ask_respond(frame, seat, Timing.RESOLVE_BEFORE_EFFECT, ctx)
'''


def test_a_synthetic_unregistered_window_makes_the_guard_red() -> None:
    """探针①：一条没登记的新窗口（候选派发 / 主公技声明 / 真询问）必须被报出。"""
    hits = scan_source(PROBE_SOURCE, "src/sgs/probe.py")

    assert sum(hits.values()) == 3, f"探针源码应恰好扫出 3 处：{hits}"
    kinds = {site[0] for site in hits}
    assert kinds == {"dispatch", "declaration", "ask"}, kinds

    known, table_problems = registered_sites()
    assert not table_problems
    problems = unregistered(hits, known)
    assert len(problems) == 3, f"未登记站点没有被全部报出——审计空转：{problems}"

    # 反面自检：把同样三处登记进临时表后必须变绿（否则"红"可能只是格式错）。
    assert unregistered(hits, dict(hits)) == []
    assert stale(hits, dict(hits)) == []


def test_a_second_dispatch_in_the_same_function_changes_the_count() -> None:
    """探针②：同一函数里**多一处**同形派发不能靠"所属同名"蒙混——计数要变红。"""
    source = '''\
def probe_twice(state):
    first = ModifierContext(subject=0, scope=CandidateScope.DODGE, respond_required="dodge")
    second = ModifierContext(subject=0, scope=CandidateScope.DODGE, respond_required="dodge")
    return first, second
'''
    hits = scan_source(source, "src/sgs/probe.py")
    doubled = [site for site, count in hits.items() if count == 2]
    assert doubled, f"探针源码应有一处站点出现 2 次：{hits}"

    known = {site: 1 for site in hits}
    problems = stale(hits, known)
    assert any("站点数变了" in problem for problem in problems), problems


# ---------------------------------------------------------------------------
# 断言 4：抽公共件之后【蛊惑】的行为逐字不变（四档语境都给候选）
# ---------------------------------------------------------------------------

PACK = load_content_pack(deck_profile="standard_108")
"""档位纪律：显式选 108 档（与 test_hero_feng 的【蛊惑】用例同一档）。"""


def _provider_state():
    """座位 1 = 于吉移动版（手里一张【闪】）；其余座位只是填充。"""
    hand = (cards_of_type(PACK, "dodge")[0],)
    trigger = cards_of_type(PACK, "slash")[0]
    filler = [
        card
        for card in range(len(PACK.deck.cards) - 1, -1, -1)
        if card not in {*hand, trigger}
    ][-2:]
    used = {*hand, trigger, *filler}
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(trigger,)),
            SeatSpec(hero="yuji_mobile", hand=hand, hp=3, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(PACK, *sorted(used)),
        turn_seat=0,
    )


def _offered(actions, skill_id: str = GUOHUO) -> bool:
    return any(
        isinstance(action, ActivateSkill) and action.skill_id == skill_id
        for action in actions
    )


def test_guohuo_provider_still_offers_in_all_four_scopes() -> None:
    """【蛊惑】的提供者分支改写为共用辅助后，四档语境的候选面逐字不变。

    四档 = :data:`CARD_REQUEST_SCOPES`（``DODGE`` / ``RESPOND`` / ``PEACH`` / ``NULLIFY``）；
    每一档都读同一份"这扇窗口要一张什么牌"（``ctx.respond_required``），故这张表就是
    "引擎把哪些窗口接上了提供者"的清单。改判（``REJUDGE``）不在其中（断言 5）。
    """
    state = _provider_state()

    assert _offered(legal_actions_for(state, 1, QueryKind.DODGE, answers_seq=1)), (
        "需要【闪】时【蛊惑】仍进候选（与【八卦阵】同一姿态）"
    )
    assert _offered(
        legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.BARBARIAN_INVASION,
                required="slash",
                mode=RespondMode.PLAY,
            ),
        )
    ), "需要打出【杀】的响应窗口仍进候选"
    assert _offered(
        legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.BORROWED_SWORD,
                required="slash",
                mode=RespondMode.USE,
                use_targets=(2,),
            ),
        )
    ), "借刀（mode=USE、够得着 Bn）的提供者分支仍进候选"
    assert _offered(
        legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.NULLIFY,
                required="nullify",
                mode=RespondMode.USE,
            ),
        )
    ), "无懈的收集式窗口仍进候选"
    assert _offered(
        legal_actions_for(state, 1, QueryKind.PEACH, answers_seq=1, dying=0)
    ), "濒死求【桃】仍进候选（本票不动的第三扇窗口）"


def test_the_provider_windows_declaration_is_the_one_the_shared_helper_reads() -> None:
    """提供者只声明"我在哪些窗口参与"：声明的 key 必须解析到真实的要牌语境。"""
    from sgs.skills import active as active_module

    declare = active_module.GUOHUO_PROVIDER_WINDOWS
    assert isinstance(declare, ProviderWindows)
    assert declare.scopes == CARD_REQUEST_SCOPES, (
        "【蛊惑】的声明面必须恰是四档要牌语境（抽公共件后行为不变）"
    )
    # 声明只能指向"允许提供者参与"的窗口（登记表说了算）。
    by_key = {row.key: row for row in CARD_REQUEST_WINDOWS}
    for key in declare.keys:
        assert key in by_key, f"声明的窗口 key 不在登记表里：{key!r}"
        assert by_key[key].providers, f"登记表说 {key!r} 不让提供者参与"


# ---------------------------------------------------------------------------
# 断言 5：改判窗口"登记但排除"
# ---------------------------------------------------------------------------


def test_the_refusal_windows_are_registered_as_not_participating() -> None:
    """改判（``REJUDGE``）登记为**不参与**：它不是使用 / 打出，判据是颜色 / 牌名而不是牌名。

    登记（而不是删掉这一行）的理由：新加一个改判类窗口时，``WindowCause`` 的闭集断言会逼
    它走过登记表，不会被静默跳过；同时这一行写清"提供者 / 转化都不参与"。
    """
    refusal = [row for row in CARD_REQUEST_WINDOWS if WindowCause.REJUDGE in row.causes]
    assert len(refusal) == 1, f"改判窗口应恰有一行登记：{[row.key for row in refusal]}"
    row = refusal[0]
    assert row.kind == REQUEST_WINDOW
    assert row.scope is None, "改判没有候选派发（_respond 的 REJUDGE 分支在派发之前就返回）"
    assert row.modes == (RespondMode.REJUDGE,)
    assert row.axis is None, "改判既不是使用也不是打出 ⇒ 没有'需要 / 可以'这一轴"
    assert not row.providers, "改判不让提供者参与（理由写在该行的 reason 里；转化技也没有受理面）"
    assert any(word in row.reason for word in ("代替", "改判")), row.reason

    # 行为面：改判窗口的候选里没有任何提供者候选（CANDIDATES 派发根本不发生）。
    state = _provider_state()
    actions = legal_actions_for(
        state,
        1,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.REJUDGE,
            mode=RespondMode.REJUDGE,
            required=None,
        ),
    )
    assert not _offered(actions), f"改判窗口不该出现提供者候选：{list(actions)}"

    # 共享判据面：非要牌语境（逐张牌 / 出牌阶段）拿不到 required。
    assert required_card(ModifierContext(subject=0, scope=CandidateScope.CARD)) is None
    assert required_card(ModifierContext(subject=0, scope=CandidateScope.PLAY)) is None
    assert required_card(ModifierContext(subject=0, respond_required="dodge")) is None
    assert required_card(
        ModifierContext(subject=0, scope=CandidateScope.DODGE, respond_required="dodge")
    ) == "dodge"


# ---------------------------------------------------------------------------
# 断言 5：五处 A 类窗口都接了询问策略的预筛口（table-ux-overhaul 票 05）
# ---------------------------------------------------------------------------
#
# spec §2 Q3 = (i)「只问合格者 + 同窗一次呈现」。落地方式**不是**在每扇窗口里各写一份
# "这个座位有没有牌 / 有没有转化能力"的判据，而是五处窗口在发问**之前**统一过票 02 的
# 预筛口，并把窗口类别位（`skip_if_empty=True`）显式交给它——判据只有一处（那个预览读的
# 就是窗口真发问时用的 `legal_actions_for`）。本断言钉"五处都接了、且都带类别位"：
# 少接一处 ⇒ 那扇窗口在网页会话里仍会向空手座位发询问（spec §1 #8 的报障原样回来）。
#
# **类别位只有一处**（R2 的统一形状）：RESPOND 那三处窗口用 `respond_context(..., 
# skip_if_empty=True)` 建**一份** `RespondContext`，同一个对象既交给预筛口、也交给随后那次
# `ask_respond`——所以这里断的**不是**预筛调用的关键字，而是"这份载荷带类别位、且两个入口
# 收到的是同一个变量"。【闪】/ 求桃的候选载荷不是 `RespondContext`（`DodgeContext` /
# `PeachContext`），它们的类别位仍是 `should_ask_query` 的关键字参数。

A_CLASS_WINDOWS: tuple[tuple[str, str, str], ...] = (
    ("src/sgs/engine/steps.py", "_collected_response_window", "should_ask_respond"),
    ("src/sgs/engine/steps.py", "_dodge_window", "should_ask_query"),
    ("src/sgs/engine/steps.py", "near_death_ask", "should_ask_query"),
    ("src/sgs/skills/lord_request.py", "_run_lord_request", "should_ask_respond"),
    ("src/sgs/effects/ops.py", "resolve_duel", "should_ask_respond"),
    ("src/sgs/effects/ops.py", "_resist_or_damage", "should_ask_respond"),
)
"""五处 A 类窗口（无懈 / 闪 / 求桃 / 护驾求助 / 决斗·南蛮·万箭）→ (路径, 函数, 预筛口)。

`_resist_or_damage` 是【南蛮】与【万箭】共用的主体，故六行覆盖"五处窗口"。
预筛口分两个名字是因为候选载荷分家：RESPOND 用 `RespondContext`（`should_ask_respond`），
【闪】/ 求桃用 `DodgeContext` / `PeachContext`（`should_ask_query`）。
"""

PrefilterSite = tuple[str, str, str, str]
"""``(路径, 函数, 预筛口名, 交给它的载荷变量名)``；``should_ask_query`` 那一格为空串。"""

ContextSite = tuple[str, str, str]
"""``(路径, 函数, 载荷变量名)``：``respond_context(..., skip_if_empty=True)`` 的声明点。"""


def scan_prefilter_sites() -> tuple[set[PrefilterSite], set[ContextSite]]:
    """全树扫 A 类窗口的两个事实源。

    返回 ``(prefilter_sites, context_sites)``：

    - ``prefilter_sites`` = 每个 ``should_ask_respond`` / ``should_ask_query`` 调用点，附带
      它收到的窗口载荷**变量名**（第二个位置实参，必须是 ``ast.Name``——内联构造的载荷没法
      和随后那次发问是**同一个对象**）；
    - ``context_sites`` = 带 ``skip_if_empty=True`` 的 ``respond_context(...)`` 调用点及其
      赋值目标名。

    判据分家：``should_ask_query`` 的类别位仍是关键字参数（这里照旧只收带
    ``skip_if_empty=True`` 的调用）；``should_ask_respond`` 的类别位住在载荷里。
    """
    prefilter_sites: set[PrefilterSite] = set()
    context_sites: set[ContextSite] = set()
    for file in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        parents = _parent_map(tree)
        rel = file.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee(node)
            if name == "respond_context":
                keywords = {
                    key.arg: key.value for key in node.keywords if key.arg is not None
                }
                bit = keywords.get("skip_if_empty")
                if not (isinstance(bit, ast.Constant) and bit.value is True):
                    continue
                holder = parents.get(node)
                target = getattr(holder, "targets", [None])[0] if holder is not None else None
                if not isinstance(target, ast.Name):
                    continue  # 内联构造：没有"同一个对象"可断
                context_sites.add((rel, _owner(node, parents), target.id))
                continue
            if name not in ("should_ask_respond", "should_ask_query"):
                continue
            keywords = {key.arg: key.value for key in node.keywords if key.arg is not None}
            payload = ""
            if name == "should_ask_respond":
                second = node.args[1] if len(node.args) > 1 else None
                assert isinstance(second, ast.Name), (
                    f"{rel} :: {_owner(node, parents)} 的预筛调用没有把窗口载荷交给一个变量"
                    "（内联构造 ⇒ 它与随后那次发问不是同一个对象）"
                )
                payload = second.id
            else:
                bit = keywords.get("skip_if_empty")
                if not (isinstance(bit, ast.Constant) and bit.value is True):
                    continue
            prefilter_sites.add((rel, _owner(node, parents), name, payload))
    return prefilter_sites, context_sites


def _ask_payloads(tree: ast.AST) -> set[str]:
    """一份 AST 里每个 ``ask_respond`` 调用收到的载荷变量名（第 4 个位置实参）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee(node) != "ask_respond":
            continue
        if len(node.args) > 3 and isinstance(node.args[3], ast.Name):
            names.add(node.args[3].id)
    return names


def test_every_a_class_window_consults_the_ask_policy_prefilter() -> None:
    """五处 A 类窗口逐个认领预筛口；多出来的（没登记的）预筛调用同样判红。

    RESPOND 那三处还多断一条**结构**：预筛口与随后的 ``ask_respond`` 收到的是**同一个**
    载荷变量，而这份载荷是带 ``skip_if_empty=True`` 建出来的——"同一个值传给两处"因此不再
    靠调用方自觉（票 02 的字段有了真正的读取方）。
    """
    expected = set(A_CLASS_WINDOWS)
    prefilter_sites, context_sites = scan_prefilter_sites()
    sites = {(path, owner, name) for path, owner, name, _payload in prefilter_sites}

    missing = expected - sites
    assert not missing, (
        "这些 A 类窗口没有接询问策略的预筛口（或没给 `skip_if_empty=True`）：\n"
        + "\n".join(f"  - {path} :: {owner} → {name}" for path, owner, name in sorted(missing))
        + "\n\n票 05 的接线口径：发问**之前**先调一次预筛；RESPOND 那三处要用同一个 "
        "`respond_context(..., skip_if_empty=True)` 建的载荷。"
        "缺了它，网页会话里空手座位仍会收到只有 Pass 的询问。"
    )
    unexpected = sites - expected
    assert not unexpected, (
        "这些预筛调用点不在 A 类窗口登记表里（新窗口 ⇒ 先在 A_CLASS_WINDOWS 里认领）：\n"
        + "\n".join(f"  - {path} :: {owner} → {name}" for path, owner, name in sorted(unexpected))
    )

    # 同一个对象：预筛收到的载荷变量必须由本函数里带类别位的 `respond_context` 建出来，
    # 并且随后那次 `ask_respond` 收的还是它。
    for path, owner, name, payload in sorted(prefilter_sites):
        if name != "should_ask_respond":
            continue
        assert (path, owner, payload) in context_sites, (
            f"{path} :: {owner} 交给预筛口的载荷 {payload!r} 不是本函数里带 "
            "`skip_if_empty=True` 的 `respond_context` 建的（类别位只有一个落点）"
        )
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
        assert payload in _ask_payloads(tree), (
            f"{path} :: {owner} 的预筛口收 {payload!r}、`ask_respond` 却收了另一个载荷："
            "两处必须是**同一个对象**（否则预筛看的面 ≠ 真发出去的面）。"
        )
    # 反向：带类别位的载荷不能建了不用（死声明 = 又回到"没人读的字段"）。
    used = {(path, owner, payload) for path, owner, _name, payload in prefilter_sites}
    assert context_sites <= used or not context_sites, (
        "这些 `skip_if_empty=True` 的窗口载荷没有交给预筛口（建了不用）："
        f"{sorted(context_sites - used)}"
    )
