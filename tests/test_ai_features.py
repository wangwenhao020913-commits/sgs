"""特征提取器与权重结构（`ai-view` 票 08a；spec §2.7 / §2.12）。

票面八条判据，逐条落在下面：

1. **parity（最强的一条）**：全部权重取手写占位值时，``features · weights`` 在固定询问批上
   **逐 float 等于**既有打分器的 ``Score`` 总分，且**逐项**对得上——"特征漏了一项"因此是红灯。
2. **B 族不许另算**：``features.py`` 里没有距离 / 斩杀 / 记牌的重新实现（源码扫描住
   ``tests/test_source_discipline.py`` 第十节）；本文件只钉"B 的值确实来自既有打分器"。
3. **A 族完备性**：``SkillFlow`` 的每条流水线（逐叶子步骤走一遍）与每个 ``Hook.timing``
   都产出一个键，且锚有值——漏一条即红。
4. **C 族只有 5 个关系类**：闭集、机械展开，无人工挑选的交互项。
5. **0 样本不归零**：新键读到 ``anchor``（手写值），不是 ``0``。
6. **尾部追加**：新增一行键不改任何既有键的数值。
7. **确定性**：同输入同输出；跨 ``PYTHONHASHSEED`` 逐字节。
8. **不接进决策路径**：``heuristic`` 不 import 本模块（读点表不动、金录像零字节变化由
   ``tests/test_goldens.sh`` 与全量档兜住，票面记读数）。

**为什么用真实对局做 parity 批**：票面要的是"同一批询问上逐 float"，而特征的值来自**既有
打分器**——构造视图只能覆盖想得到的分支，真对局能撞出 `DISTRIBUTE` 之外的十类询问。4 个
种子 × 5 人局 ≈ 2 s（实测 0.5 s/局），故它留在票档而不进慢片。
"""

from __future__ import annotations

import ast
import hashlib
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sgs.agents import features, weights
from sgs.agents import heuristic as heuristic_scoring
from sgs.data import Identity, Mode, RuleConfig, load_content_pack
from sgs.effects import legal_actions_for
from sgs.engine import Engine
from sgs.engine.rng import ai_stream
from sgs.state import (
    CardRef,
    Cause,
    DistributeContext,
    DistributeQuery,
    FactKind,
    Phase,
    PlayerPublic,
    PlayerView,
    PublicEvent,
    QueryKind,
    UseCard,
    project,
)
from sgs.timing import Timing

from sgs.agents import HeuristicPlayer

ROOT = Path(__file__).resolve().parent.parent
PACK = load_content_pack(deck_profile="standard_108")
SEEDS: tuple[int, ...] = (7008, 7009, 7010, 7011)
"""parity 批次：4 个 5 人全启发式种子（实测覆盖九类询问、A / C 三族都有货）。"""

FRAME_DIGEST = "c918d4102b1cb0a138b1122c3b4abae76788555f988d195111ec0775c3bb6b81"
"""整批特征向量的 sha256（``kind|actor|seq|action|候选分|逐项``）：**逐字节**的回归钉。

它钉的是**新产物**（特征帧），不是决策：票 08b 会正当地改它（权重变了、候选集合也变了），
那时按新读数重录并在票面写清"这是行为改动"。本票它必须与"只加特征层"这一版逐字节相同。

**`ai-view` 票 14 的重录**：拆 / 顺按装备的**效果**定价 ⇒ 选位 / 选牌换人 ⇒ 后续候选集合变化
（5529 → 4506 那条 parity 批同源），逐字节摘要因此从 `679c0883…` 变到 `c918d410…`（拆 / 顺
之外的锦囊与选牌步**不记**那一项，故项集只在拆 / 顺候选上多一格）。新增的
B 族项名 ``b|目标装备价值`` 由 §3.4 的"新 `Score.add` 项名自动扩键空间"覆盖（锚 1.0，不必回
补表）；这是**行为改动**，不是特征层漏项——``test_the_breakdown_is_the_existing_breakdown_item_for_item``
仍逐项对得上。
"""


# ---------------------------------------------------------------------------
# 批次：4 局 × 全部候选（候选分 + 逐项特征一次算完）
# ---------------------------------------------------------------------------


class _Batch:
    """parity 批的读数：逐候选行 + 违规清单。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.violations: list[str] = []
        self.candidates = 0
        self.queries = 0
        self.kinds: dict[str, int] = {}
        self.b_keys: set[str] = set()
        self.position_instability: list[str] = []
        self.linear_breaks: list[str] = []


@pytest.fixture(scope="module")
def _batch() -> _Batch:
    """固定批次跑**一次**：每个候选的（既有分数, 特征帧, 线性分）。

    观察点是**两处独立入口**：``heuristic._SCORERS``（生产派发表，与 ``respond`` 同一条路）
    给的 float，与 ``features.frame`` 自己走同一条派发算出的特征帧。两者互不引用，故
    "特征漏了一项"会让它们对不上。

    **批次轨迹与权重表无关**：``FRAME_DIGEST`` 钉的是**提取层**（项名 / 次序 / 键形状），
    不是某一版权重下的决策路径。08c 落仓 B 族权重后 ``respond`` 会走带权分支、轨迹随权重
    变；把 ``has_fitted_b`` 临时压成 False（= 08a 的手写路径）后，四局询问序列与落权重前
    逐字相同，摘要因此仍只对提取层负责。
    """
    batch = _Batch()
    original_has_fitted_b = weights.has_fitted_b
    weights.has_fitted_b = lambda: False

    def run(query) -> None:
        batch.queries += 1
        kind = QueryKind(query.kind).name
        batch.kinds[QueryKind(query.kind).name] = batch.kinds.get(kind, 0) + 1
        position: tuple[tuple[str, float], ...] | None = None
        scorer = heuristic_scoring._SCORERS[QueryKind(query.kind)]
        for action in query.legal_actions:
            with heuristic_scoring.Score.tracing():
                value = scorer(query, action)
            store = features.frame(query, action)
            batch.candidates += 1
            batch.b_keys.update(key for key, _value in store.items)
            # 08b 起 B 族权重不再是 1.0：这里的等价性判据留在**提取层**（分项之和 == 既有分），
            # 不再经权重；"整数权重 vs 浮点参考"那条 parity 住 test_ai_weights_parity.py。
            got = features.unweighted_candidate_score(store)
            batch.lines.append(
                f"{kind}|{query.actor}|{query.seq}|{action!r}|{got!r}|{store.items!r}"
            )
            if got != float(value):
                batch.violations.append(
                    f"{kind}@{query.actor}#{query.seq}: 候选分 {got!r} != 既有分 {float(value)!r}"
                )
            if features.linear(store) != features.candidate_score(
                store
            ) + features.position_score(store):
                batch.linear_breaks.append(f"{kind}@{query.actor}#{query.seq}")
            # A / C 只读 view ⇒ 同一询问的全部候选上**逐键逐值**相同。
            here = tuple(store.position().items())
            if position is None:
                position = here
            elif here != position:
                batch.position_instability.append(f"{kind}@{query.actor}#{query.seq}")

    original = HeuristicPlayer.respond

    def respond(player, query, _original=original):
        run(query)
        return _original(player, query)

    HeuristicPlayer.respond = respond
    try:
        for seed in SEEDS:
            engine = Engine.new_game(
                seed=seed,
                rule_config=RuleConfig(player_count=5),
                pack=PACK,
                hero_pool=(),
            )
            engine.run(
                [HeuristicPlayer(ai_stream(seed, seat)) for seat in range(5)]
            )
    finally:
        HeuristicPlayer.respond = original
        weights.has_fitted_b = original_has_fitted_b
    return batch


# ---------------------------------------------------------------------------
# ① parity
# ---------------------------------------------------------------------------


def test_the_batch_actually_reached_the_scorers(_batch):
    """覆盖元断言：批次空转时，parity 会"零违规"地通过。"""
    assert _batch.queries >= 200, f"只跑到 {_batch.queries} 个询问；批次可能没接上派发"
    assert _batch.candidates >= 400, f"只跑到 {_batch.candidates} 个候选"
    assert len(_batch.kinds) >= 6, f"只覆盖了 {sorted(_batch.kinds)} 类询问"
    assert len(_batch.b_keys) >= 30, (
        f"只见到 {len(_batch.b_keys)} 个 B 族项名；特征提取可能没摊开"
    )


def test_features_times_weights_equals_the_existing_score_float_for_float(_batch):
    """parity：``features · weights`` 与既有 ``Score`` 总分**逐 float 相等**。

    权重全在锚上（``B_ANCHOR = 1.0``）⇒ 这就是"特征层 + 权重层是既有打分的等价写法"。
    任何一项被漏掉 / 被改名 / 被另算一遍，和都会变——它不是"同一决策"，是**同一个数**。
    """
    assert not _batch.violations, "；".join(_batch.violations[:5])


def test_every_candidate_splits_into_candidate_and_position_parts(_batch):
    """``linear == 候选分 + 局面分``：两个和分开算（不重排浮点加法）。"""
    assert not _batch.linear_breaks, "；".join(_batch.linear_breaks[:5])


def test_the_position_terms_are_identical_for_every_candidate_of_a_query(_batch):
    """A / C 族只读 ``query.view`` ⇒ 同一询问的全部候选上逐键相同 ⇒ **不改变 argmax**。

    这条也是"为什么票面 parity 说的是候选那一半"的机械依据：局面项在每个候选上加同一个
    常数（08a 不把它们下沉进候选分；08b / 09 用它们做局面读数）。
    """
    assert not _batch.position_instability, "；".join(_batch.position_instability[:5])


def test_the_frame_digest_is_pinned(_batch):
    """整批特征向量逐字节可复现（同输入同输出；跨进程见下一条）。"""
    digest = hashlib.sha256("\n".join(_batch.lines).encode()).hexdigest()
    assert digest == FRAME_DIGEST, (
        "特征帧变了：先查 B 族的项名 / 累加次序与 A / C 的键形状，不要重录摘要"
    )


def test_the_breakdown_is_the_existing_breakdown_item_for_item():
    """**逐项**对得上：``frame.b()`` == 既有 trace 的分项（同名累加），一项不多一项不少。

    期望值由测试自己从**第二次** trace 归并（与 ``features.b_family`` 不共用代码路径），
    故"提取层少摊了一项"或"多项式累加"都会在这里对不上。
    """
    original = HeuristicPlayer.respond
    checked = 0
    mismatches: list[str] = []

    def respond(player, query, _original=original):
        nonlocal checked
        scorer = heuristic_scoring._SCORERS[QueryKind(query.kind)]
        for action in query.legal_actions:
            with heuristic_scoring.Score.tracing():
                value = scorer(query, action)
            expected: dict[str, float] = {}
            for item, item_value in value.items:
                key = weights.b_key(item)
                expected[key] = expected.get(key, 0.0) + item_value
            got = features.frame(query, action).b()
            checked += 1
            if dict(got) != expected:
                mismatches.append(
                    f"{QueryKind(query.kind).name}#{query.seq}: {got} != {expected}"
                )
        return _original(player, query)

    HeuristicPlayer.respond = respond
    try:
        for seed in SEEDS:
            engine = Engine.new_game(
                seed=seed,
                rule_config=RuleConfig(player_count=5),
                pack=PACK,
                hero_pool=(),
            )
            engine.run([HeuristicPlayer(ai_stream(seed, seat)) for seat in range(5)])
    finally:
        HeuristicPlayer.respond = original

    assert checked >= 400, f"只对了 {checked} 个候选"
    assert not mismatches, "；".join(mismatches[:5])


def test_the_distribute_query_also_has_a_constructed_parity_case():
    """``DISTRIBUTE`` 在 4 个种子的批里出现 **0 次**：构造一局补上，十类询问全过 parity。

    判据与批次**同一条**（候选分逐 float 相等 + 逐项对得上），不是"另一套更松的断言"：
    否则"十类询问都有 scorer"这句话就只剩九类有证据。
    """
    from support import SeatSpec, build_state

    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=5),
        seats=[
            SeatSpec(identity=Identity.LORD),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.LOYALIST),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.LOYALIST),
        ],
        deck=(),
        turn_seat=0,
    )
    for seat in (1, 3):
        state.players[seat].identity_visible = True
    payload = DistributeContext(
        skill_id="hero.shen_zhouyu.yeyan", seats=(0, 1), max_total=2, per_seat_max=2
    )
    query = DistributeQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="请为角色分配点数。",
        legal_actions=legal_actions_for(
            state, 0, QueryKind.DISTRIBUTE, answers_seq=1, payload=payload
        ),
        view=project(state, 0),
        skill_id=payload.skill_id,
        seats=payload.seats,
        max_total=payload.max_total,
        per_seat_max=payload.per_seat_max,
    )
    scorer = heuristic_scoring._SCORERS[QueryKind.DISTRIBUTE]
    checked = 0
    for action in query.legal_actions:
        with heuristic_scoring.Score.tracing():
            value = scorer(query, action)
        store = features.frame(query, action)
        expected: dict[str, float] = {}
        for item, item_value in value.items:
            key = weights.b_key(item)
            expected[key] = expected.get(key, 0.0) + item_value
        checked += 1
        assert features.candidate_score(store) == float(value), action
        assert dict(store.b()) == expected, action

    assert checked >= 3, f"只构造出 {checked} 个分配候选"


def test_every_score_item_literal_in_the_source_maps_to_a_b_key():
    """B 族的键空间对**源码里的每个项名字面量**都是全函数（含批次里没走到的分支）。

    判据是静态的（AST 扫 ``heuristic.py`` 的 ``add`` / ``_item_score`` / ``_net_score`` /
    ``transform`` 首参），故"只在我踩到的那几条分支上碰巧成立"不算数。项名含分隔符
    ``|`` 之类的写法会当场炸——那是键语法的边界，不是运行期惊喜。
    """
    names = _score_item_literals()

    assert len(names) >= 90, f"只认出 {len(names)} 个项名字面量；AST 判据可能失效了"
    for name in sorted(names):
        mapped = features.b_family([(name, 1.0)])

        assert mapped == {weights.b_key(name): 1.0}, f"项名 {name!r} 映射成了 {mapped}"
        assert weights.anchor(weights.b_key(name)) == weights.B_ANCHOR


def _score_item_literals() -> set[str]:
    """``heuristic.py`` 里记分调用首参的**字符串字面量**（静态扫描，不跑对局）。"""
    source = (ROOT / "src" / "sgs" / "agents" / "heuristic.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if callee not in {"add", "_item_score", "_net_score", "transform"}:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    # ``_PRICE_ITEM`` 是项名的**常量引用**（"牌价"），不在字面量里；它的真源仍是那个常量。
    names.add(heuristic_scoring._PRICE_ITEM)
    return names


# ---------------------------------------------------------------------------
# ② 确定性与"没接进决策路径"
# ---------------------------------------------------------------------------


def test_the_extraction_is_deterministic_across_python_hash_seeds():
    """跨 ``PYTHONHASHSEED`` 逐字节一致：真跑一局，打印三族特征与候选分。"""
    script = """
import json

from sgs.agents import HeuristicPlayer, features
from sgs.agents import heuristic as heuristic_scoring
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.engine.rng import ai_stream

pack = load_content_pack(deck_profile="standard_108")
rows = []
original = HeuristicPlayer.respond


def respond(player, query):
    for action in query.legal_actions:
        store = features.frame(query, action)
        # **不排序**：映射的顺序（首次出现序）本身就是确定性判据的一部分。
        rows.append([str(query.kind), query.seq, str(action.kind),
                     features.candidate_score(store),
                     list(store.as_mapping().items())])
    return original(player, query)


HeuristicPlayer.respond = respond
engine = Engine.new_game(
    seed=7008, rule_config=RuleConfig(player_count=5), pack=pack,
    hero_pool=(),
)
engine.run([HeuristicPlayer(ai_stream(7008, seat)) for seat in range(5)])
print(json.dumps(rows, sort_keys=True))
"""
    outputs = []
    for seed in ("0", "1"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.append(completed.stdout)

    assert outputs[0] == outputs[1], "跨 PYTHONHASHSEED 的特征读数不一致"
    assert len(outputs[0]) > 1000, "子进程没打印出读数；脚本可能没跑起来"


def test_the_weights_table_is_wired_into_the_decision_path():
    """08b 的接线：``heuristic.py`` **只** import ``weights``（B 族读口），不 import ``features``。

    B 族的值必须来自既有的 ``Score.add`` 分项（票 02 的 trace），所以决策路径不需要特征
    提取器本身；``features`` 是工具侧 / parity 用的另一条入口。空表时 ``respond`` 走原来的
    快路径（逐 float 等于 08a），有拟合值时走 ``_weighted_candidate_score``。
    """
    source = (ROOT / "src" / "sgs" / "agents" / "heuristic.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in {"features", "weights"}:
                imported.add(node.module)
            for alias in node.names:
                if alias.name in {"features", "weights"}:
                    imported.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.rsplit(".", 1)[-1]
                if name in {"features", "weights"}:
                    imported.add(name)

    assert "weights" in imported, "08b 必须把权重表接进决策路径"
    assert "features" not in imported, (
        "决策路径只该读 weights；features 是工具侧的提取入口，接进来会多算一遍 A / C"
    )


# ---------------------------------------------------------------------------
# ③ A 族完备性：每条流水线的每个步骤 + 每个 Hook.timing
# ---------------------------------------------------------------------------


def _declared_flows():
    """``skill_id -> [(timing 或 None, SkillFlow)]``（与 ``tools/dump_public_surface.py`` 同源）。"""
    from sgs.skills.active import ACTIVE_SKILLS
    from sgs.skills.preactivation import FLOWS

    flows: dict[str, list] = {skill_id: list(entries) for skill_id, entries in FLOWS.items()}
    for skill in ACTIVE_SKILLS:
        flow = getattr(skill, "flow", None)
        if flow is not None:
            flows.setdefault(skill.skill_id, []).append((None, flow))
    return flows


def test_every_flow_step_yields_an_a_key():
    """``SkillFlow`` 的**每个步骤**都产出一个键：逐流水线、逐叶子步骤走一遍。

    主动技的效果编号是 ``(技能, 流水线名, 0)``——``Cause.order`` 对主动技恒 0
    （``Engine._skill_cause``：一条流水线就是一条效果），故"每个步骤"在这里是**逐步骤
    核两件事**：

    1. 这一步在票 05 的可见性表里有一行（``STEP_VISIBILITY[技能][步骤名]``）——步骤是**真的
       存在过声明里**，不是循环变量空转；
    2. 它所属的那条效果编号能成键、且锚是个有限数。

    **不许空转**：``steps_seen`` 单独计数并下界断言——流水线全部退化成"零叶子步骤"时，
    ``flows_seen`` 仍是 217，只有 ``steps_seen`` 会塌到 0。
    """
    from sgs.skills.flow import flow_steps
    from sgs.skills.visibility import STEP_VISIBILITY

    flows = _declared_flows()
    flows_seen = 0
    steps_seen = 0
    problems: list[str] = []
    for skill_id in sorted(flows):
        for _timing, flow in flows[skill_id]:
            flows_seen += 1
            steps = flow_steps(flow)
            steps_seen += len(steps)
            try:
                key = weights.a_key(skill_id, flow.name, 0, "move:DRAW", "unknown")
            except ValueError as error:
                problems.append(f"{skill_id}:{flow.name} 成不了键：{error}")
                continue
            if not math.isfinite(weights.anchor(key)):
                problems.append(f"{skill_id}:{flow.name} 的锚不是有限数")
            # 没有叶子步骤的流水线是**合法的**（效果直接从发动回调跑，如【奸雄】）：它没有
            # 步骤要覆盖，但它的效果编号已在上一步核过。
            for step in steps:
                row = STEP_VISIBILITY.get(skill_id, {}).get(step.key)
                if row is None:
                    problems.append(f"{skill_id}:{flow.name} 的步骤 {step.key!r} 没登记")

    assert flows_seen >= 200, f"只走过 {flows_seen} 条流水线；派生目标可能指错了"
    assert steps_seen >= 200, (
        f"只走到 {steps_seen} 个叶子步骤（{flows_seen} 条流水线）——"
        "流水线全退化成'零步骤'时这条会塌，`flows_seen` 不会"
    )
    assert not problems, "；".join(problems[:5])


def test_every_hook_timing_yields_an_a_key():
    """每个 ``Hook.timing`` 都产出一个键（序 = 同一时机内的第几条，与 ``cause_for_hook`` 同源）。

    也核**每个 ``Timing`` 值都在键里成得了一个键**（不只是被某条钩子用到的那几个），以及
    ``Hook`` 的序可以从声明派生（同机时的第几条）——这条派生真源在
    ``sgs.skills.catalog.cause_for_hook``，本测试按同一口径复算并逐条比对。
    """
    from sgs.skills.catalog import CATALOG, cause_for_hook
    from sgs.timing import Timing

    created = 0
    problems: list[str] = []
    timings = set()
    for skill_id in sorted(CATALOG):
        definition = CATALOG[skill_id]
        for hook in definition.hooks:
            timings.add(hook.timing.value)
            peers = [peer for peer in definition.hooks if peer.timing is hook.timing]
            order = peers.index(hook)
            created += 1
            try:
                key = weights.a_key(skill_id, hook.timing.value, order, "delta:hp_change", "self")
            except ValueError as error:
                problems.append(f"{skill_id}@{hook.timing.value}[{order}] 成不了键：{error}")
                continue
            if not isinstance(weights.anchor(key), float):
                problems.append(f"{skill_id}@{hook.timing.value}[{order}] 的锚不是数")

    assert created >= 200, f"只走过 {created} 条钩子；派生目标可能指错了"
    assert len(timings) >= 20, f"只见到 {len(timings)} 个时机值"
    assert not problems, "；".join(problems[:5])

    # 帧内接缝和钩子共用同一个 `Cause` 生产者（`cause_for_hook` 的 `seam:<kind>` 那一档），
    # 故它们也必须成键——漏一条就是"这一类公开事实永远没有特征"。
    seams = 0
    for skill_id in sorted(CATALOG):
        for seam in CATALOG[skill_id].seams:
            peers = [peer for peer in CATALOG[skill_id].seams if peer.kind is seam.kind]
            trigger = f"seam:{seam.kind.value}"
            weights.anchor(
                weights.a_key(skill_id, trigger, peers.index(seam), "delta:hp_change", "self")
            )
            seams += 1
    assert seams >= 1, "一条接缝都没走到；派生目标可能指错了"

    # 每个 Timing 值都能成键（含今天没有钩子的时机：新钩子挂上去时键语法已经就位）。
    for timing in Timing:
        weights.anchor(
            weights.a_key("hero.none.skill", timing.value, 0, "delta:hp_change", "self")
        )

    # 序的派生与引擎侧同一口径：拿真实实例算一次 `cause_for_hook`，逐字段比对。
    from sgs.data import load_content_pack as _load
    from sgs.engine import Engine
    from sgs.skills.catalog import resolve_instances

    engine = Engine.new_game(
        seed=7011,
        rule_config=RuleConfig(player_count=5),
        pack=_load(deck_profile="standard_108"),
        hero_pool=(),
    )
    checked = 0
    for instance in resolve_instances(engine.state):
        definition = CATALOG[instance.skill_id]
        for hook in definition.hooks:
            cause = cause_for_hook(instance, hook)
            order = [peer for peer in definition.hooks if peer.timing is hook.timing].index(hook)
            assert (cause.trigger, cause.order) == (hook.timing.value, order)
            weights.anchor(
                weights.a_key(instance.skill_id, cause.trigger, cause.order, "move:DRAW", "self")
            )
            checked += 1
    assert checked >= 4, f"只核到 {checked} 条钩子；实例解析可能指错了"


# ---------------------------------------------------------------------------
# ④ C 族：恒定 5 值、机械展开
# ---------------------------------------------------------------------------


def _view(identities, seat=1):
    players = tuple(
        PlayerPublic(
            seat=index,
            hp=4,
            hp_max=4,
            hand_count=2,
            alive=True,
            equip=(),
            judge=(),
            identity=identity,
        )
        for index, identity in enumerate(identities)
    )
    return PlayerView(
        seat=seat,
        players=players,
        self_hand=(),
        deck_count=60,
        discard=(),
        processing=(),
        turn_seat=0,
        phase=Phase.PLAY,
    )


def _cause(skill_id="hero.caocao.jianxiong", trigger="DAMAGE_TAKE", order=0):
    return Cause(
        owner=3, skill_id=skill_id, source=f"hero:{skill_id}", trigger=trigger, order=order
    )


def _answer(seq, actor, targets, fact_class="answer:UseCard", cause=None):
    return PublicEvent(
        seq=seq,
        kind=FactKind.ANSWER,
        fact_class=fact_class,
        actor=actor,
        targets=targets,
        window="PLAY_PHASE",
        cause=cause,
        payload=UseCard(
            answers_seq=seq,
            card=CardRef(card_id=seq, type="slash", name="slash"),
            targets=targets,
        ),
    )


def test_the_relation_classes_are_exactly_the_closed_five():
    """C / A 族的关系轴是**闭集 5 值**，且锚表与它同序同键。"""
    assert weights.RELATION_CLASSES == (
        "self",
        "known_ally",
        "unknown",
        "known_enemy",
        "lord",
    )
    assert tuple(weights.C_ANCHORS) == weights.RELATION_CLASSES
    for relation in weights.RELATION_CLASSES:
        assert weights.anchor(weights.c_key("move:DRAW", relation)) == weights.C_ANCHORS[relation]
    # C 族的锚是**既有关系系数**：与启发式的那一个幅度逐值同步（抄了字面量就要有漂移断言）。
    assert weights.C_ANCHORS["known_enemy"] == heuristic_scoring._RELATION_FOCUS
    assert weights.C_ANCHORS["known_ally"] == -heuristic_scoring._RELATION_FOCUS


def test_the_relation_class_reads_the_same_relations_the_scorer_does():
    """5 个关系类各归各档：自己 / 主公 / 已明盟友 / 已明敌人 / 未明。

    判据走 ``_identity_relation``（打分路径用的**同一个函数**），故这里同时钉住"关系类不会
    与打分器的敌我判断说两套话"。
    """
    view = _view((Identity.LORD, Identity.LOYALIST, Identity.LOYALIST, Identity.REBEL, None))

    assert features.relation_class(view, 1) == "self"
    assert features.relation_class(view, 0) == "lord"
    assert features.relation_class(view, 2) == "known_ally"
    assert features.relation_class(view, 3) == "known_enemy"
    assert features.relation_class(view, 4) == "unknown"
    # 越界座位不猜（与 I1 的"算不出就中性"同一条口径）。
    assert features.relation_class(view, 9) == "unknown"


def test_the_relation_class_speaks_pve_too():
    """PvE 的阵营全公开 ⇒ 读的是同一条"已亮明"档，读出确定的友 / 敌（不新增读点）。"""
    view = _view((Identity.ALLY, Identity.ENEMY, Identity.ALLY), seat=0)

    assert features.relation_class(view, 1) == "known_enemy"
    assert features.relation_class(view, 2) == "known_ally"


def test_the_c_family_expands_events_times_relations_mechanically():
    """C 族 = 公开事实 × 关系类的**计数**（多目标逐座位计一次；无目标退回 actor）。"""
    view = _view((Identity.LORD, Identity.LOYALIST, Identity.LOYALIST, Identity.REBEL, None))
    events = (
        # 打到我身上（自己是"自己"那一档）
        _answer(1, 3, (1,), cause=_cause()),
        # 一次打两个人：主公 + 已明敌人
        _answer(2, 3, (0, 3)),
        # 引擎级事件（没有起因）：actor 自己落 self
        _answer(3, 1, (), fact_class="answer:EndPhase"),
    )
    with_history = view.model_copy(update={"public_history": events})

    counts = features.c_family(with_history)

    assert dict(counts) == {
        weights.c_key("answer:UseCard", "self"): 1.0,
        weights.c_key("answer:UseCard", "lord"): 1.0,
        weights.c_key("answer:UseCard", "known_enemy"): 1.0,
        weights.c_key("answer:EndPhase", "self"): 1.0,
    }


def test_the_c_key_space_is_every_registered_fact_class_times_the_five_relations():
    """静态键空间 = 登记过的**事实类** × 5 个关系类；每个键的锚都读得出来。

    事实类的闭集是 ``ENGINE_LEVEL_FACT_CLASSES``（新增 delta / 动作类型必须在
    ``sgs.state`` 登记，未登记即红）——故"新出现一种事实"会自动进键空间，不需要回来补表。
    """
    from sgs.state import ENGINE_LEVEL_FACT_CLASSES as classes

    assert len(classes) >= 35, f"只登记了 {len(classes)} 个事实类；枚举目标可能指错了"
    keys = [
        weights.c_key(fact_class, relation)
        for fact_class in sorted(classes)
        for relation in weights.RELATION_CLASSES
    ]
    assert len(keys) == len(classes) * 5
    for key in keys:
        assert isinstance(weights.anchor(key), float)


def test_an_unknown_relation_or_fact_class_shape_fails_closed():
    """键语法 **fail-closed**：认不出的前缀 / 第六个关系类 / 空的项名一律炸，不静默落默认。"""
    with pytest.raises(ValueError):
        weights.family("x|move:DRAW|self")
    with pytest.raises(ValueError):
        weights.family("")
    with pytest.raises(ValueError):
        weights.b_key("")
    with pytest.raises(ValueError):
        weights.b_key("a|b")
    with pytest.raises(ValueError):
        weights.c_key("move:DRAW", "known_neutral")
    with pytest.raises(ValueError):
        weights.a_key("hero.x.y", "TIMING", -1, "move:DRAW", "self")
    with pytest.raises(ValueError):
        weights.anchor("c|move:DRAW")  # 少了关系类


# ---------------------------------------------------------------------------
# ⑤ 0 样本不归零
# ---------------------------------------------------------------------------


def test_a_key_without_samples_reads_its_anchor_not_zero():
    """**0 样本不归零**：没有样本的键读到 ``anchor``（手写值），不是 0 分。

    否则新武将上线的头几天，AI 恰好对"最新那条技能"没有任何意见。三族各来一条：B = 1.0、
    C = 既有关系系数、A = 查不到标量时的典型价（``unregistered_anchor``，今天 2.0）。
    所选键必须**真的不在** 08b 落仓的 A / C 表里——上线权重后，用``move:LOSE`` 这类
    已观测事实会测到"拟合值"，而不是"0 样本回锚"。
    """
    keys = {
        weights.b_key("一个还没人登记过的项名"): weights.B_ANCHOR,
        # 事实类用**虚构**的（`move:LOSE` 已经在 08b 的 A / C 首轮拟合里有值了）。
        weights.c_key("move:__NEVER_OBSERVED__", "known_enemy"): weights.C_ANCHORS[
            "known_enemy"
        ],
        # 查不到的技能读**典型价**（常量档众数 2.0），不是 0；4.0 的【奸雄】常量锚由下一条
        # `test_an_unregistered_or_shaped_skill_still_reads_a_nonzero_anchor` 覆盖。
        weights.a_key(
            "hero.nosuch.never_observed", "TURN_START", 0, "move:DRAW", "self"
        ): weights.unregistered_anchor(),
    }

    for key, expected in keys.items():
        assert weights.weight(key) == expected
        assert weights.weight(key) != 0.0

    # "没有样本"是真的：两类表里都没有这一行（不是"表里恰好写了锚"）。
    assert weights.SHARED_B.value(weights.b_key("一个还没人登记过的项名")) is None
    a_key = weights.a_key(
        "hero.nosuch.never_observed", "TURN_START", 0, "move:DRAW", "self"
    )
    c_key = weights.c_key("move:__NEVER_OBSERVED__", "known_enemy")
    assert weights.IDENTITY_AC.value(a_key) is None
    assert weights.IDENTITY_AC.value(c_key) is None


def test_an_unregistered_or_shaped_skill_still_reads_a_nonzero_anchor():
    """锚**是全函数**，而且新技能 / 形状档技能读到的是**手写值的典型价**，不是 0。

    票面："0 样本的键读出来是 anchor，不是 0"。新武将上线时它的技能正好落这一格——读到 0
    就成了"AI 恰好对最新那条技能没有任何意见"。典型价 = 常量档的**众数**（今天 2.0，163 个
    常量里出现 45 次）；这条把读数钉成字面量，表变了要人重读一次。
    """
    assert weights.unregistered_anchor() == 2.0
    assert weights.unregistered_anchor() > 0.0
    unregistered = weights.a_key("hero.nosuch.nothing", "TURN_START", 0, "move:DRAW", "self")
    assert weights.anchor(unregistered) == weights.unregistered_anchor()
    # 形状档（【仁德】的手写值依赖局面，不是一个数）不冒充常量锚，但也不落 0。
    shaped = weights.a_key("hero.liubei.rende", "PHASE_PLAY_IDLE", 0, "move:DRAW", "self")
    assert weights.anchor(shaped) == weights.unregistered_anchor()
    # 常量档则给出那个常量（【奸雄】4.0）。
    constant = weights.a_key("hero.caocao.jianxiong", "DAMAGE_TAKE", 0, "move:DRAW", "self")
    assert weights.anchor(constant) == 4.0


# ---------------------------------------------------------------------------
# ⑥ 尾部追加 + 分块
# ---------------------------------------------------------------------------


def test_appending_a_row_never_changes_an_existing_value():
    """**只许尾部追加**：新增一行键不改任何既有键的数值（``fit`` 返回新表，旧表逐位不变）。"""
    table = weights.SHARED_B.fit(weights.b_key("第一行"), 2000).fit(
        weights.b_key("第二行"), 3000
    )
    before_keys = table.keys()
    before_values = table.values()

    after = table.fit(weights.b_key("第三行"), 4000)

    assert after.keys() == (*before_keys, weights.b_key("第三行"))
    assert after.values()[: len(before_values)] == before_values
    assert table.keys() == before_keys, "``fit`` 不许原地改既有表"
    assert table.values() == before_values


def test_a_duplicate_or_foreign_family_row_fails_closed():
    """重复键 / 串族 / 非 float 一律构造期炸（静默覆盖会让"哪个数算数"没人答得上来）。"""
    table = weights.SHARED_B.fit(weights.b_key("一行"), 1500)

    with pytest.raises(ValueError):
        table.fit(weights.b_key("一行"), 2500)
    with pytest.raises(ValueError):
        table.fit(weights.c_key("move:DRAW", "self"), 1000)
    with pytest.raises(ValueError):
        weights.WeightTable(
            name="bad",
            families=(weights.FAMILY_B,),
            rows=((weights.b_key("x"), 1.5),),
        )
    with pytest.raises(ValueError):
        weights.WeightTable(
            name="bad",
            families=(weights.FAMILY_B,),
            rows=((weights.c_key("move:DRAW", "self"), 1000),),
        )


def test_the_mode_blocks_are_one_per_mode():
    """A / C 族**按模式各一份**：``MODE_BLOCKS`` 的键集恰等于 ``Mode``，各块只收 A / C 族。"""
    assert set(weights.MODE_BLOCKS) == set(Mode)
    for mode, table in weights.MODE_BLOCKS.items():
        assert table.families == (weights.FAMILY_A, weights.FAMILY_C), mode
        for key, value in table.rows:
            assert weights.family(key) in (weights.FAMILY_A, weights.FAMILY_C)
            assert isinstance(value, int) and not isinstance(value, bool)
    assert weights.SHARED_B.families == (weights.FAMILY_B,)
    for key, value in weights.SHARED_B.rows:
        assert weights.family(key) == weights.FAMILY_B
        assert isinstance(value, int) and not isinstance(value, bool)


def test_the_shared_b_block_is_read_for_every_mode():
    """B 族跨模式共享：同一 B 键在三种模式下读到**同一个数**（A / C 才分块）。"""
    key = weights.b_key("伤害牌底价")
    key_a = weights.a_key("hero.caocao.jianxiong", "DAMAGE_TAKE", 0, "move:LOSE", "self")

    assert len({weights.weight(key, mode) for mode in Mode}) == 1
    # A 族的读法不随模式变（B 共享、A / C 分块；只有身份局那块可能有拟合值）。
    assert len({weights.weight(key_a, mode) for mode in Mode}) == 1


# ---------------------------------------------------------------------------
# ⑦ 金录像零字节变化（票面要求显式断言）
# ---------------------------------------------------------------------------


def test_the_golden_replays_are_byte_identical():
    """47 条标准 108 金录像**逐字节不变**——真源仍是 ``tools/check_goldens.sh``。

    本票不碰引擎，但 ``heuristic.py`` 改过一行（``scorer.anchor``）：票面要求的"显式断言"
    因此不能只是"应该自动成立"。这里把那条脚本接进本票的用例（实测 ≈1 s），不另抄一份
    判据——`tests/test_goldens.sh` 在门禁里跑的仍是同一支脚本。
    """
    completed = subprocess.run(
        ["bash", "tools/check_goldens.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "逐字节不变" in completed.stdout, completed.stdout


# ---------------------------------------------------------------------------
# ⑧ 兜底：B 族没有第二个来源
# ---------------------------------------------------------------------------


def test_a_scorer_that_does_not_trace_is_rejected():
    """打分器没开 trace / 返回裸 float ⇒ 直接炸，不静默给一条零特征。

    "零特征"会让 candidate_score 变成 0 分而不是"没有 B 族"——那种静默会把一整类候选
    压到 Pass 之下，且没有任何红灯。
    """
    captured: list[object] = []
    original = HeuristicPlayer.respond

    def respond(player, query, _original=original):
        if not captured:
            captured.append(query)
        return _original(player, query)

    HeuristicPlayer.respond = respond
    try:
        engine = Engine.new_game(
            seed=SEEDS[0], rule_config=RuleConfig(player_count=5), pack=PACK, hero_pool=()
        )
        engine.run([HeuristicPlayer(ai_stream(SEEDS[0], seat)) for seat in range(5)])
    finally:
        HeuristicPlayer.respond = original

    query = captured[0]
    action = query.legal_actions[0]
    # ``extract`` 是 ``frame().as_mapping()`` 的扁平视图（"每项一个 float"的那份映射）。
    assert features.extract(query, action) == features.frame(query, action).as_mapping()

    kind = QueryKind(query.kind)
    table = heuristic_scoring._SCORERS
    heuristic_scoring._SCORERS = type(table)(
        {**table, kind: lambda _query, _action: 0.0}
    )
    try:
        with pytest.raises(ValueError, match="没有交出分项"):
            features.frame(query, query.legal_actions[0])
    finally:
        heuristic_scoring._SCORERS = table
