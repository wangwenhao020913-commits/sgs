"""``Cancelled`` 读取点的机械护栏（dead-actor-in-flight 票 06）。

引擎对**窗口外已阵亡**的应答者返回 :data:`sgs.engine.Cancelled`：它不是 ``Pass``、不是空选、
也不是 ``SUSPEND``，而是"这一问根本没有发生"（`spec.md` §1.2 / Q8 / Q11）。技能与效果层读到
它必须**停当前效果段**，不能按"已接受的动作"继续（发动 / 摸牌 / 弃牌 / 伤害）。fuzz 只核
``QueryRecord`` 与 delta，把 ``Cancelled`` 当空选读**不会**变红——故本文件把这件事做成机械闸。

扫描口径（与 ``tests/test_card_request_windows.py`` 同一姿态：**只读 AST + 源码文本**，
不 import ``src/sgs`` 来做扫描）：

- **站点** = ``src/sgs/skills/**`` / ``src/sgs/effects/**`` 与 ``src/sgs/engine/steps.py`` 里
  的一次 ``host.ask_*`` 调用（:data:`ASK_METHODS` 的九个助手名）；
- **适配判据** = 同一条函数里，这次发问的**返回值绑定名**在发问之后被一条
  ``host.cancelled(...)`` 读到（``answer = host.ask_option(...)`` ⇒ 之后有
  ``host.cancelled(answer)``），或这次发问直接嵌在 ``host.cancelled(...)`` 的实参里；
- **基线** = :data:`UNADAPTED`：``(相对路径, 函数名, 该函数里未适配站点的条数)``。
  它记录"今天还没有读这个哨兵的站点"——**允许存在，但只许缩小**。同一批函数还冻结
  :data:`ADAPTED`（``(相对路径, 函数名, 已适配条数)``），见下。

为什么用**计数**而不只是 ``(路径, 函数)``：函数粒度会让"往一个已登记的函数里再新写一处
发问"静默通过。计数把同一函数里的新增站点也变成红：登记 1、实得 2 ⇒ 红，作者要么读
``host.cancelled(...)``、要么把计数登记上去（登记 = 评审可见的"承认这里仍会把取消当已接受
动作读"）。

**为什么还要第二个计数（票 06 复核发现）**：只冻结未适配条数时，"**修好一处旧站点 +
新加一处未适配站点**（同一函数）"一减一增、条数不变 ⇒ 这道闸全绿。故对 :data:`UNADAPTED`
里的每个函数**再冻结已适配条数**（:data:`ADAPTED`）：修好一处会让它 +1，新加一处已适配的
也会 +1 ⇒ 那一对改动不再互相抵消。条数变了就重生成基线——登记是一次显式评审决定，不是
"修"。只给已登记的（仍未适配的）函数留档：没有未适配站点的函数本来就只能靠"新增未适配
站点"变红，不需要这一列。

**怎么重生成基线**：``.venv/bin/python tests/test_cancelled_reads.py`` 会打印两张表的
Python 字面量；把输出整体替换 :data:`UNADAPTED` / :data:`ADAPTED` 即可（这是**登记**，
不是"修"）。基线只许缩小：源码里修好一处而没把计数调小，闸也会红（"基线可以缩小了"），
这样"缩小"不会被忘记；已适配条数的任何变化同样要重生成（见上）。

**盲区（如实登记；`## Answer` 里同条）**：

1. 只认"名字可追"的读取。``return host.ask_...`` / 把发问当别的调用实参的站点，即使调用方
   读了 ``Cancelled`` 也算未适配（保守方向：宁可误红，登记即可）。
2. 返回值若改名 / 先存帧私有再读出（``frame.data[k] = answer`` … ``host.cancelled(frame.data[k])``），
   本扫描认不出，会算未适配。
3. 同一函数里两处发问若把结果绑到**同一个名字**（重赋值），后一处之后的那条
   ``host.cancelled`` 会让前一处也被算成已适配——今天没有这种形状。
4. 只查"读没读"，不查"读对了没有"：读到哨兵后仍落进"发动 / 摸两张"分支的，本闸看不见
   （那由各站点的行为断言兜住）。
5. 不扫 ``src/sgs/engine/game.py``（询问助手自己的实现，哨兵在那里定义）与 web / AI 层。
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

ASK_METHODS = frozenset(
    {
        "ask_query",
        "ask_arrange",
        "ask_distribution",
        "ask_respond",
        "ask_selection",
        "ask_skill_activation",
        "ask_option",
        "ask_options",
        "ask_seats",
    }
)
"""返回 ``Cancelled`` 的询问助手（``src/sgs/engine/game.py`` 的九个入口）。

新加一个发问助手时把它加进来——否则新助手写出来的站点整类不受本闸覆盖。
"""

SCAN_ROOTS = (
    ("src/sgs/skills", True),
    ("src/sgs/effects", True),
    ("src/sgs/engine/steps.py", False),
)
"""``(相对路径, 是否递归目录)``：技能 / 效果层全扫，引擎侧只扫响应窗口所在的 ``steps.py``。"""

Site = tuple[str, str]
"""``(相对路径, 所属函数点分名)``。"""

# ---------------------------------------------------------------------------
# 冻结基线：仍未适配 ``Cancelled`` 的站点（路径 + 函数 + 未适配条数）。
#
# 生成方式：`.venv/bin/python tests/test_cancelled_reads.py`。
# 只能缩小：函数里补上 `host.cancelled(...)` 之后，把该行删掉或把条数调小；本闸会检查
# "源码里已经不变红、基线还记着"（stale）并判红。
# ---------------------------------------------------------------------------

UNADAPTED: tuple[tuple[str, str, int], ...] = (
    ("src/sgs/effects/ops.py", "_take_one_zone_card", 1),
    ("src/sgs/effects/ops.py", "resolve_borrowed_sword", 1),
    ("src/sgs/effects/ops.py", "resolve_duel", 1),
    ("src/sgs/engine/steps.py", "_rejudge_window", 1),
    ("src/sgs/engine/steps.py", "near_death_ask", 1),
    ("src/sgs/engine/steps.py", "rank_compare_pick", 1),
    ("src/sgs/skills/active.py", "_ask_multi_target_seats", 1),
    ("src/sgs/skills/active.py", "_dimeng_effect", 1),
    ("src/sgs/skills/active.py", "_guolun_effect", 1),
    ("src/sgs/skills/active.py", "_hui_shi_effect", 2),
    ("src/sgs/skills/active.py", "_huishi_effect", 3),
    ("src/sgs/skills/active.py", "_jieyue_damage_branch", 1),
    ("src/sgs/skills/active.py", "_jieyue_effect", 1),
    ("src/sgs/skills/active.py", "_jieyue_mark_branch", 1),
    ("src/sgs/skills/active.py", "_poxi_effect", 1),
    ("src/sgs/skills/active.py", "_qianxun_ask_seat", 1),
    ("src/sgs/skills/active.py", "_qianxun_pick_name", 1),
    ("src/sgs/skills/active.py", "_quhu_effect", 1),
    ("src/sgs/skills/active.py", "_shejian_ask_seat", 1),
    ("src/sgs/skills/active.py", "_shejian_pick_card", 1),
    ("src/sgs/skills/active.py", "_wuyi_fix_hand", 1),
    ("src/sgs/skills/active.py", "_zuoxing_effect", 3),
    ("src/sgs/skills/active.py", "guohuo_provider", 1),
    ("src/sgs/skills/awakening.py", "_qinxue_effect", 1),
    ("src/sgs/skills/awakening.py", "_tianyi_effect", 1),
    ("src/sgs/skills/catalog.py", "_dispatch", 1),
    ("src/sgs/skills/catalog.py", "_double_sword", 2),
    ("src/sgs/skills/catalog.py", "_ice_sword", 1),
    ("src/sgs/skills/damage_after.py", "_ganglie", 2),
    ("src/sgs/skills/damage_after.py", "_guixin", 1),
    ("src/sgs/skills/damage_after.py", "_jie_ganglie", 1),
    ("src/sgs/skills/damage_after.py", "_jie_kuanggu_mobile", 1),
    ("src/sgs/skills/damage_after.py", "_jie_yiji", 2),
    ("src/sgs/skills/damage_after.py", "_liyu_effect", 1),
    ("src/sgs/skills/damage_after.py", "_wangxi", 1),
    ("src/sgs/skills/damage_after.py", "_yiji", 2),
    ("src/sgs/skills/death_after.py", "_wuhun_death", 1),
    ("src/sgs/skills/flow.py", "_flow_step_arrange", 2),
    ("src/sgs/skills/flow.py", "_flow_step_cards", 1),
    ("src/sgs/skills/flow.py", "_flow_step_option", 1),
    ("src/sgs/skills/flow.py", "_flow_step_provide", 1),
    ("src/sgs/skills/flow.py", "_flow_step_seats", 1),
    ("src/sgs/skills/flow.py", "_flow_step_seats_once", 1),
    ("src/sgs/skills/hero_caocao.py", "_changyi", 1),
    ("src/sgs/skills/hero_caocao.py", "_changyi_boost", 1),
    ("src/sgs/skills/hero_caocao.py", "_changyi_choice", 1),
    ("src/sgs/skills/hero_caocao.py", "_changyi_punish", 2),
    ("src/sgs/skills/hero_caocao.py", "_hongzhi_adjust", 1),
    ("src/sgs/skills/hero_guanyu.py", "_ask_seat", 1),
    ("src/sgs/skills/hero_guanyu.py", "_xinhan_effect", 1),
    ("src/sgs/skills/hero_guanyu.py", "_yiyun_first_stage", 1),
    ("src/sgs/skills/hero_lin.py", "_haoshi_effect", 2),
    ("src/sgs/skills/hero_lin.py", "_yinghun_effect", 1),
    ("src/sgs/skills/hero_mou_gong.py", "_finish_effect", 2),
    ("src/sgs/skills/hero_yijiang_2011.py", "_pojun_effect", 1),
    ("src/sgs/skills/hero_yujin.py", "_dingluan", 2),
    ("src/sgs/skills/hero_zone.py", "_lianying", 1),
    ("src/sgs/skills/judgment_loop.py", "_luoshen", 1),
    ("src/sgs/skills/lord_request.py", "_run_lord_request", 1),
    ("src/sgs/skills/move_after.py", "MoveAfterSkill.handle", 1),
    ("src/sgs/skills/negation.py", "_fenwei_handler", 1),
    ("src/sgs/skills/phase_body.py", "_bingyi_seats", 1),
    ("src/sgs/skills/phase_body.py", "_jie_luoyi_effect", 1),
    ("src/sgs/skills/phase_body.py", "_jie_shensu_choose", 1),
    ("src/sgs/skills/phase_body.py", "_jie_shensu_pay_equipment", 1),
    ("src/sgs/skills/phase_body.py", "_jie_shensu_use_slash", 1),
    ("src/sgs/skills/phase_body.py", "_shelie_effect", 1),
    ("src/sgs/skills/phase_body.py", "_shensu_pay_equipment", 1),
    ("src/sgs/skills/phase_body.py", "_shensu_use_slash", 1),
    ("src/sgs/skills/response_window.py", "_jie_tieqi", 1),
    ("src/sgs/skills/shen_zhugeliang.py", "_dawu_apply", 1),
    ("src/sgs/skills/start.py", "_tiancheng", 1),
    ("src/sgs/skills/use_after.py", "_congtao", 2),
    ("src/sgs/skills/use_after.py", "_yajiao", 5),
    ("src/sgs/skills/zuoci.py", "_ask_choice", 1),
)

ADAPTED: tuple[tuple[str, str, int], ...] = (
    ("src/sgs/effects/ops.py", "_take_one_zone_card", 0),
    ("src/sgs/effects/ops.py", "resolve_borrowed_sword", 1),
    ("src/sgs/effects/ops.py", "resolve_duel", 1),
    ("src/sgs/engine/steps.py", "_rejudge_window", 0),
    ("src/sgs/engine/steps.py", "near_death_ask", 0),
    ("src/sgs/engine/steps.py", "rank_compare_pick", 0),
    ("src/sgs/skills/active.py", "_ask_multi_target_seats", 0),
    ("src/sgs/skills/active.py", "_dimeng_effect", 0),
    ("src/sgs/skills/active.py", "_guolun_effect", 0),
    ("src/sgs/skills/active.py", "_hui_shi_effect", 0),
    ("src/sgs/skills/active.py", "_huishi_effect", 0),
    ("src/sgs/skills/active.py", "_jieyue_damage_branch", 0),
    ("src/sgs/skills/active.py", "_jieyue_effect", 0),
    ("src/sgs/skills/active.py", "_jieyue_mark_branch", 0),
    ("src/sgs/skills/active.py", "_poxi_effect", 0),
    ("src/sgs/skills/active.py", "_qianxun_ask_seat", 0),
    ("src/sgs/skills/active.py", "_qianxun_pick_name", 0),
    ("src/sgs/skills/active.py", "_quhu_effect", 0),
    ("src/sgs/skills/active.py", "_shejian_ask_seat", 0),
    ("src/sgs/skills/active.py", "_shejian_pick_card", 0),
    ("src/sgs/skills/active.py", "_wuyi_fix_hand", 0),
    ("src/sgs/skills/active.py", "_zuoxing_effect", 0),
    ("src/sgs/skills/active.py", "guohuo_provider", 0),
    ("src/sgs/skills/awakening.py", "_qinxue_effect", 0),
    ("src/sgs/skills/awakening.py", "_tianyi_effect", 0),
    ("src/sgs/skills/catalog.py", "_dispatch", 0),
    ("src/sgs/skills/catalog.py", "_double_sword", 0),
    ("src/sgs/skills/catalog.py", "_ice_sword", 0),
    ("src/sgs/skills/damage_after.py", "_ganglie", 0),
    ("src/sgs/skills/damage_after.py", "_guixin", 0),
    ("src/sgs/skills/damage_after.py", "_jie_ganglie", 0),
    ("src/sgs/skills/damage_after.py", "_jie_kuanggu_mobile", 0),
    ("src/sgs/skills/damage_after.py", "_jie_yiji", 0),
    ("src/sgs/skills/damage_after.py", "_liyu_effect", 0),
    ("src/sgs/skills/damage_after.py", "_wangxi", 0),
    ("src/sgs/skills/damage_after.py", "_yiji", 0),
    ("src/sgs/skills/death_after.py", "_wuhun_death", 0),
    ("src/sgs/skills/flow.py", "_flow_step_arrange", 0),
    ("src/sgs/skills/flow.py", "_flow_step_cards", 0),
    ("src/sgs/skills/flow.py", "_flow_step_option", 0),
    ("src/sgs/skills/flow.py", "_flow_step_provide", 0),
    ("src/sgs/skills/flow.py", "_flow_step_seats", 0),
    ("src/sgs/skills/flow.py", "_flow_step_seats_once", 0),
    ("src/sgs/skills/hero_caocao.py", "_changyi", 0),
    ("src/sgs/skills/hero_caocao.py", "_changyi_boost", 0),
    ("src/sgs/skills/hero_caocao.py", "_changyi_choice", 0),
    ("src/sgs/skills/hero_caocao.py", "_changyi_punish", 0),
    ("src/sgs/skills/hero_caocao.py", "_hongzhi_adjust", 0),
    ("src/sgs/skills/hero_guanyu.py", "_ask_seat", 0),
    ("src/sgs/skills/hero_guanyu.py", "_xinhan_effect", 0),
    ("src/sgs/skills/hero_guanyu.py", "_yiyun_first_stage", 0),
    ("src/sgs/skills/hero_lin.py", "_haoshi_effect", 0),
    ("src/sgs/skills/hero_lin.py", "_yinghun_effect", 0),
    ("src/sgs/skills/hero_mou_gong.py", "_finish_effect", 0),
    ("src/sgs/skills/hero_yijiang_2011.py", "_pojun_effect", 0),
    ("src/sgs/skills/hero_yujin.py", "_dingluan", 0),
    ("src/sgs/skills/hero_zone.py", "_lianying", 0),
    ("src/sgs/skills/judgment_loop.py", "_luoshen", 0),
    ("src/sgs/skills/lord_request.py", "_run_lord_request", 0),
    ("src/sgs/skills/move_after.py", "MoveAfterSkill.handle", 0),
    ("src/sgs/skills/negation.py", "_fenwei_handler", 0),
    ("src/sgs/skills/phase_body.py", "_bingyi_seats", 0),
    ("src/sgs/skills/phase_body.py", "_jie_luoyi_effect", 0),
    ("src/sgs/skills/phase_body.py", "_jie_shensu_choose", 0),
    ("src/sgs/skills/phase_body.py", "_jie_shensu_pay_equipment", 0),
    ("src/sgs/skills/phase_body.py", "_jie_shensu_use_slash", 0),
    ("src/sgs/skills/phase_body.py", "_shelie_effect", 0),
    ("src/sgs/skills/phase_body.py", "_shensu_pay_equipment", 0),
    ("src/sgs/skills/phase_body.py", "_shensu_use_slash", 0),
    ("src/sgs/skills/response_window.py", "_jie_tieqi", 0),
    ("src/sgs/skills/shen_zhugeliang.py", "_dawu_apply", 0),
    ("src/sgs/skills/start.py", "_tiancheng", 0),
    ("src/sgs/skills/use_after.py", "_congtao", 0),
    ("src/sgs/skills/use_after.py", "_yajiao", 0),
    ("src/sgs/skills/zuoci.py", "_ask_choice", 0),
)


# ---------------------------------------------------------------------------
# 扫描器（只读 AST）
# ---------------------------------------------------------------------------


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _owner(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    """站点所属函数 / 类的点分名（嵌套函数 / 方法都带外层名；模块级 ⇒ ``<module>``）。"""
    parts: list[str] = []
    cursor: ast.AST = node
    while cursor in parents:
        holder = parents[cursor]
        if isinstance(holder, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(holder.name)
        cursor = holder
    return ".".join(reversed(parts)) or "<module>"


def _ask_method(call: ast.Call) -> str | None:
    """``host.ask_*`` 的助手名；别的调用 ⇒ ``None``。"""
    function = call.func
    if not isinstance(function, ast.Attribute):
        return None
    if function.attr not in ASK_METHODS:
        return None
    # 只认宿主调用（``host`` / ``self`` 之外的接收者今天不存在，仍允许任意属性接收者：
    # 判定依据是"调的是不是那九个助手"）。
    return function.attr


def _bound_names(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> frozenset[str]:
    """这次发问的返回值**绑定到哪些名字**（``x = ...`` / ``x: T = ...`` / ``(x := ...)``）。

    绑定形状认不出（``return host.ask_...`` / 当别的调用的实参）⇒ 空集合，按"未适配"算。
    """
    holder = parents.get(call)
    if isinstance(holder, ast.Assign):
        target = holder.targets[0]
    elif isinstance(holder, ast.AnnAssign):
        target = holder.target
    elif isinstance(holder, ast.NamedExpr):
        target = holder.target
    else:
        return frozenset()
    return frozenset(node.id for node in ast.walk(target) if isinstance(node, ast.Name))


def _cancelled_reads(tree: ast.AST) -> tuple[ast.Call, ...]:
    return tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cancelled"
    )


def _names_in(node: ast.AST) -> frozenset[str]:
    return frozenset(child.id for child in ast.walk(node) if isinstance(child, ast.Name))


def _reads_this_answer(
    call: ast.Call,
    reads: tuple[ast.Call, ...],
    bound: frozenset[str],
) -> bool:
    """同一条函数里，这次发问的绑定名是否在**发问之后**被 ``host.cancelled`` 读到。

    两种形状算适配：

    - ``host.cancelled(host.ask_option(...))``（发问直接嵌在读取里）；
    - ``answer = host.ask_option(...)`` 之后出现 ``host.cancelled(answer)``（或读取的实参
      里出现这个名字，例如 ``host.cancelled(host.accepted(answer))``）。
    """
    for read in reads:
        if read is call:
            continue
        if any(child is call for child in ast.walk(read)):
            return True
        if not bound:
            continue
        if (read.lineno, read.col_offset) <= (call.lineno, call.col_offset):
            continue
        if bound & _names_in(read):
            return True
    return False


def scan_source_counts(source: str, path: str) -> tuple[Counter[Site], Counter[Site]]:
    """扫一份源码，返回 ``(未适配, 已适配)`` 两张 ``{(路径, 函数): 条数}`` 表。"""
    tree = ast.parse(source)
    parents = _parent_map(tree)
    reads = _cancelled_reads(tree)
    unadapted: Counter[Site] = Counter()
    adapted: Counter[Site] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _ask_method(node) is None:
            continue
        site = (path, _owner(node, parents))
        if _reads_this_answer(node, reads, _bound_names(node, parents)):
            adapted[site] += 1
        else:
            unadapted[site] += 1
    return unadapted, adapted


def scan_source(source: str, path: str) -> Counter[Site]:
    """扫一份源码，返回 ``{(路径, 函数): 未适配站点条数}``。"""
    return scan_source_counts(source, path)[0]


def scan_tree_counts(root: Path = REPO_ROOT) -> tuple[Counter[Site], Counter[Site]]:
    """扫 :data:`SCAN_ROOTS`（``.py``，跳过 ``__pycache__``），返回两张计数表。"""
    unadapted: Counter[Site] = Counter()
    adapted: Counter[Site] = Counter()
    for relative, recursive in SCAN_ROOTS:
        target = root / relative
        files = sorted(target.rglob("*.py")) if recursive else [target]
        for file in files:
            if "__pycache__" in file.parts:
                continue
            file_unadapted, file_adapted = scan_source_counts(
                file.read_text(encoding="utf-8"),
                file.relative_to(root).as_posix(),
            )
            unadapted.update(file_unadapted)
            adapted.update(file_adapted)
    return unadapted, adapted


def scan_tree(root: Path = REPO_ROOT) -> Counter[Site]:
    """扫 :data:`SCAN_ROOTS` 的未适配站点（:data:`UNADAPTED` 的生成源）。"""
    return scan_tree_counts(root)[0]


# ---------------------------------------------------------------------------
# 基线 ↔ 源码的对照
# ---------------------------------------------------------------------------


def baseline_sites(
    rows: tuple[tuple[str, str, int], ...] = UNADAPTED,
    *,
    minimum: int = 1,
) -> tuple[dict[Site, int], list[str]]:
    """把基线折成 ``{站点: 条数}``；顺带报**基线自身**的问题（重复行 / 非法条数）。"""
    registered: dict[Site, int] = {}
    problems: list[str] = []
    for path, owner, count in rows:
        site = (path, owner)
        if site in registered:
            problems.append(f"基线里有重复行：{path} :: {owner}")
        if count < minimum:
            problems.append(f"基线条数必须 ≥ {minimum}：{path} :: {owner} 记了 {count}")
        registered[site] = count
    return registered, problems


def gaps(hits: Counter[Site], known: dict[Site, int]) -> list[str]:
    """源码里未适配、基线却没登记（或登记得比实际少）的站点。"""
    problems: list[str] = []
    for site, count in sorted(hits.items()):
        expected = known.get(site)
        if expected is None:
            problems.append(
                f"未读 host.cancelled(...) 的发问站点：{site[0]} :: {site[1]}"
                f"（该函数里有 {count} 处发问没有读取消哨兵）"
            )
        elif count > expected:
            problems.append(
                f"已登记函数里又多了未适配的发问：{site[0]} :: {site[1]}"
                f"（基线 {expected} 处、实得 {count} 处）"
            )
    return problems


def stale(known: dict[Site, int], hits: Counter[Site]) -> list[str]:
    """基线登记得比源码多（包括"已经全部适配、基线还留着"）：基线只许缩小。"""
    problems: list[str] = []
    for site, expected in sorted(known.items()):
        actual = hits.get(site, 0)
        if actual < expected:
            problems.append(
                f"基线可以缩小了：{site[0]} :: {site[1]}"
                f"（基线 {expected} 处、实得 {actual} 处）——删掉这一行或把条数调小"
            )
    return problems


def adapted_drift(
    hits: Counter[Site],
    known: dict[Site, int],
    unadapted: dict[Site, int],
) -> list[str]:
    """同一批函数的**已适配**条数必须与 :data:`ADAPTED` 逐字相等（票 06 复核）。

    这条关的是"**修好一处旧站点 + 新加一处未适配站点**"的抵消通道：未适配条数一减一增
    不变，而已适配条数会 +1。只给 :data:`UNADAPTED` 里的函数留档——没有未适配站点的函数
    本来就没有这条通道（新增的未适配站点会直接落进 :func:`gaps`）。
    """
    problems: list[str] = []
    for site in sorted(unadapted):
        recorded = known.get(site)
        if recorded is None:
            problems.append(f"ADAPTED 基线缺这一行：{site[0]} :: {site[1]}")
            continue
        actual = hits.get(site, 0)
        if actual != recorded:
            problems.append(
                f"同一函数里的**已适配**发问条数变了：{site[0]} :: {site[1]}"
                f"（基线 {recorded} 处、实得 {actual} 处）——修好一处旧站点 / 新加一处都会"
                "改这个数；别让两个计数互相抵消，重生成基线"
            )
    for site in sorted(set(known) - set(unadapted)):
        problems.append(
            f"ADAPTED 基线里的这一行没有对应的未适配站点：{site[0]} :: {site[1]}"
            "——只有 UNADAPTED 里的函数才记已适配条数"
        )
    return problems


# ---------------------------------------------------------------------------
# 断言：全树站点要么读哨兵、要么在冻结基线里
# ---------------------------------------------------------------------------


def test_every_ask_site_reads_cancelled_or_is_in_the_frozen_baseline() -> None:
    """任何 ``host.ask_*`` 站点：函数里必须读 ``host.cancelled(...)``，否则登记进基线。

    红 = 新写的站点把 ``Cancelled`` 当已接受的动作读（或在已登记函数里新增了一处），
    或已登记函数的**已适配**条数与 :data:`ADAPTED` 不一致（"修一处 + 新加一处"的抵消）。
    """
    hits, adapted = scan_tree_counts()
    known, table_problems = baseline_sites()
    adapted_known, adapted_problems = baseline_sites(ADAPTED, minimum=0)
    problems = [
        *table_problems,
        *adapted_problems,
        *gaps(hits, known),
        *stale(known, hits),
        *adapted_drift(adapted, adapted_known, known),
    ]
    assert not problems, (
        "发问站点与「取消哨兵读取」基线不同步：\n"
        + "\n".join(f"  - {problem}" for problem in problems)
        + "\n\n写入 / 修改技能效果或响应窗口时：拿到 `host.ask_*` 的返回值之后必须读一次\n"
        "`host.cancelled(answer)`（取消 ≠ Pass / 空选 / SUSPEND：停当前效果段，不派生后续）；\n"
        "确实暂时做不到（例如把返回值原样 `return` 给调用方）时，把 `(路径, 函数, 条数)`\n"
        "登记进 `tests/test_cancelled_reads.py` 的 `UNADAPTED`，并在该行旁写明原因。\n"
        "改了已登记函数的读取面（修好一处 / 新加一处）时，`UNADAPTED` 与 `ADAPTED` 两张表\n"
        "都要重生成——只改一张会让两个计数互相抵消。\n"
        "重生成：`.venv/bin/python tests/test_cancelled_reads.py`。"
    )


def test_the_baseline_has_no_duplicate_or_invalid_rows() -> None:
    """基线自身的形状：一行一个站点、未适配条数 ≥ 1、已适配条数 ≥ 0。"""
    _, table_problems = baseline_sites()
    _, adapted_problems = baseline_sites(ADAPTED, minimum=0)
    assert not [*table_problems, *adapted_problems], "\n".join(
        [*table_problems, *adapted_problems]
    )


def test_the_adapted_table_covers_exactly_the_unadapted_functions() -> None:
    """两张基线表必须一一对应：只有仍未适配的函数才冻结已适配条数。"""
    unadapted_known, _ = baseline_sites()
    adapted_known, _ = baseline_sites(ADAPTED, minimum=0)
    assert sorted(adapted_known) == sorted(unadapted_known), (
        "ADAPTED 的键集必须与 UNADAPTED 逐字相等（重生成两张表）"
    )


# ---------------------------------------------------------------------------
# 合成探针：证明闸不是空转
# ---------------------------------------------------------------------------

PROBE_SOURCE = '''\
def probe_unregistered(host, frame):
    return host.ask_option(frame, 0, "t", skill_id="probe:x", options=("a",))


def probe_adapted(host, frame):
    answer = host.ask_option(frame, 0, "t", skill_id="probe:y", options=("a",))
    if host.suspended(answer):
        return "stay"
    if host.cancelled(answer):
        return None
    return answer


def probe_half_adapted(host, frame):
    first = host.ask_selection(frame, owner="k1", key="k1", actor=0, timing="t")
    if host.cancelled(first):
        return None
    second = host.ask_selection(frame, owner="k2", key="k2", actor=0, timing="t")
    return second


def probe_wrong_variable(host, frame):
    answer = host.ask_option(frame, 0, "t", skill_id="probe:z", options=("a",))
    other = 0
    if host.cancelled(other):
        return None
    return answer


def probe_nested_inside_read(host, frame):
    if host.cancelled(host.ask_option(frame, 0, "t", skill_id="probe:w", options=("a",))):
        return None
    return None
'''


def test_a_synthetic_unregistered_site_makes_the_guard_red() -> None:
    """探针①：未登记的新站点（``return host.ask_...`` / 读错变量 / 半适配）必须被报出。"""
    hits = scan_source(PROBE_SOURCE, "src/sgs/probe.py")

    assert hits == {
        ("src/sgs/probe.py", "probe_unregistered"): 1,
        ("src/sgs/probe.py", "probe_half_adapted"): 1,
        ("src/sgs/probe.py", "probe_wrong_variable"): 1,
    }, hits

    problems = gaps(hits, {})
    assert len(problems) == 3, f"探针里未登记的站点没有被全部报出——闸空转：{problems}"

    # 反面自检：把同样三处登记进临时基线后必须变绿（否则"红"可能只是格式错）。
    assert gaps(hits, dict(hits)) == []
    assert stale(dict(hits), hits) == []


def test_a_new_site_in_a_registered_function_is_still_red() -> None:
    """探针②：函数已在基线里，**再新写**一处发问也不能靠"函数名登记过"蒙混。"""
    first = '''\
def probe(host, frame):
    answer = host.ask_option(frame, 0, "t", skill_id="probe:x", options=("a",))
    return answer
'''
    second = '''\
def probe(host, frame):
    answer = host.ask_option(frame, 0, "t", skill_id="probe:x", options=("a",))
    extra = host.ask_selection(frame, owner="k", key="k", actor=0, timing="t")
    return answer, extra
'''
    registered = dict(scan_source(first, "src/sgs/probe.py"))
    assert registered == {("src/sgs/probe.py", "probe"): 1}, registered

    grown = scan_source(second, "src/sgs/probe.py")
    problems = gaps(grown, registered)
    assert len(problems) == 1 and "又多了未适配的发问" in problems[0], problems


def test_a_fixed_site_must_shrink_the_baseline() -> None:
    """探针③：基线只许缩小——源码修好了、基线还记着 ⇒ 红（不许留死账）。"""
    fixed = '''\
def probe(host, frame):
    answer = host.ask_option(frame, 0, "t", skill_id="probe:x", options=("a",))
    if host.cancelled(answer):
        return None
    return answer
'''
    hits = scan_source(fixed, "src/sgs/probe.py")
    assert hits == {}

    problems = stale({("src/sgs/probe.py", "probe"): 1}, hits)
    assert len(problems) == 1 and "基线可以缩小了" in problems[0], problems


# 探针④的两份源码：``ATTACK_AFTER`` = **修好 ``first`` + 新加未适配的 ``third``** ——
# 未适配条数因此一减一增、仍是 1 / 1，这正是复核证明的那条绿通道。
ATTACK_BEFORE = '''\
def site(host, frame):
    first = host.ask_option(frame, 0, "t", skill_id="probe:a", options=("a",))
    second = host.ask_option(frame, 0, "t", skill_id="probe:b", options=("b",))
    if host.cancelled(second):
        return None
    return first
'''

ATTACK_AFTER = '''\
def site(host, frame):
    first = host.ask_option(frame, 0, "t", skill_id="probe:a", options=("a",))
    if host.cancelled(first):
        return None
    second = host.ask_option(frame, 0, "t", skill_id="probe:b", options=("b",))
    third = host.ask_option(frame, 0, "t", skill_id="probe:c", options=("c",))
    if host.cancelled(second):
        return None
    return third
'''


def test_adapting_one_site_does_not_pay_for_a_new_unadapted_site() -> None:
    """探针④：同一函数里"修好一处旧站点 + 新加一处未适配站点"不得互相抵消。

    复核的复现形状：基线 ``{("src/sgs/skills/probe.py", "site"): 1}`` 下，把 ``first``
    补上 ``host.cancelled(...)``、同时新加一处未适配的 ``third`` ⇒ 未适配条数仍是 1，
    :func:`gaps` 与 :func:`stale` **都空**（旧闸绿）。现在 :func:`adapted_drift` 读
    已适配条数（1 → 2）⇒ 红。
    """
    site: Site = ("src/sgs/skills/probe.py", "site")
    before_unadapted, before_adapted = scan_source_counts(ATTACK_BEFORE, site[0])
    assert before_unadapted == {site: 1}, before_unadapted
    assert before_adapted == {site: 1}, before_adapted

    after_unadapted, after_adapted = scan_source_counts(ATTACK_AFTER, site[0])
    assert after_unadapted == before_unadapted, (
        "未适配条数没变——旧基线看不出这一对改动（这正是要关的通道）"
    )
    known = dict(before_unadapted)
    assert gaps(after_unadapted, known) == []
    assert stale(known, after_unadapted) == []

    problems = adapted_drift(after_adapted, dict(before_adapted), known)
    assert len(problems) == 1 and "已适配" in problems[0], problems

    # 反面自检：按 `ATTACK_AFTER` 重生成两张表后必须变绿（否则"红"可能只是格式错）。
    assert adapted_drift(after_adapted, dict(after_adapted), dict(after_unadapted)) == []


# ---------------------------------------------------------------------------
# 基线重生成入口（不经过 pytest 也能用）
# ---------------------------------------------------------------------------


def _print_baseline() -> None:
    hits, adapted = scan_tree_counts()
    print(f"# 未适配站点 {sum(hits.values())} 处、{len(hits)} 个函数")
    print("UNADAPTED: tuple[tuple[str, str, int], ...] = (")
    for (path, owner), count in sorted(hits.items()):
        print(f'    ("{path}", "{owner}", {count}),')
    print(")")
    print()
    print("# 同一批函数里的**已适配**条数（票 06 复核：与 UNADAPTED 逐键对应）")
    print("ADAPTED: tuple[tuple[str, str, int], ...] = (")
    for path, owner in sorted(hits):
        print(f'    ("{path}", "{owner}", {adapted[(path, owner)]}),')
    print(")")


if __name__ == "__main__":
    _print_baseline()
