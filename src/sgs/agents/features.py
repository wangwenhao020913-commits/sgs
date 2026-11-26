"""三族特征提取（`ai-view` 票 08a；spec §2.7 / §2.12 的"引擎算好的公开后果"）。

**特征表长什么样**（键的语法与分块见 :mod:`sgs.agents.weights`）：

- **B 族（局面轴）**：每一项与既有的 ``Score.add`` 项同名。**值来自既有实现**——本模块
  调**同一个打分器**（``heuristic._SCORERS`` 的派发表，与 ``respond`` 同一条路）、读它
  ``Score.tracing()`` 交出的分项。距离 / 斩杀线 / P(闪) / 记牌这些量**一项都不在这里重算**
  （train/serve skew 是这一路线的头号风险，spec §6）：复算就是第二份真源，必然漂。
  这也解释了 B 族的锚为什么是 1.0（``weights.B_ANCHOR``）：既有系数已经烘在项值里。
- **A 族（事件轴）**：``(技能, 触发点, 序)``（效果编号）+ 公开载荷类别 + 关系类 → **计数**。
  只从 ``PlayerView.public_history`` 的**带起因的**公开事实派生（``cause is None`` 的是
  引擎级事件：摸牌 / 回合切换 / 玩家自己的动作，归 C 族）。**没有既有对应**，是新键。
- **C 族（交互）**：公开事实类 × 关系类 → **计数**。机械展开、**不人工挑选交互项**——挑选
  就是在手写语义。

**A / C 是"局面项"，B 是"候选项"**：A / C 只读 ``query.view``，同一询问的**全部候选**上
逐键相同 ⇒ 加进总分只是给每个候选加同一个常数，**不改变 argmax**。故候选打分的口径是
:func:`candidate_score`（= B 族，逐 float 等于今天的 ``Score``），局面读数是
:func:`position_score`；08a 把它们分开（票面 parity 就是"候选那一半逐 float 相等"），
08b 接下沉到哪个总式是那一票的裁定。

**只读可见面**：本模块只碰 :class:`~sgs.state.PlayerView` 与询问自带的面。不 import
``sgs.engine`` / ``sgs.skills``，不读 ``GameState``。需要启发式的那三样（打分器派发表 /
``Score`` / ``_identity_relation``）走**函数内**的延迟 import：08b 起 ``heuristic →
features`` 是单向依赖，顶层 import 会成环；这条由
``tests/test_source_discipline.py`` 的第十节机械钉住。

**确定性**：纯函数，无 IO、无随机源、无时钟；映射一律按**首次出现序**（不用无序容器），
故同输入同输出、跨 ``PYTHONHASHSEED`` 逐字节一致。

**局面桶**（`ai-train` 票 06）：:func:`bucket` 只把询问级的两条公开量（存活数 / 是否
出牌阶段）编码成 token（``a1p0``），供样本 v3 的键展开与 08a 的读路径共用；它不改任何
既有打分，也不读 ``GameState``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sgs.data import Identity, Mode
from sgs.state import Action, PlayerView, PublicEvent, Query

from . import weights

__all__ = [
    "BUCKET_ALIVE",
    "BUCKET_ALIVE_BOUNDS",
    "BUCKET_ALIVE_TOKENS",
    "BUCKET_PHASE",
    "BUCKET_PHASE_TOKENS",
    "BUCKET_VERSION",
    "DEFAULT_BUCKET_SET",
    "FeatureFrame",
    "a_family",
    "b_family",
    "bucket",
    "bucket_dimensions",
    "c_family",
    "candidate_score",
    "extract",
    "frame",
    "linear",
    "position_score",
    "relation_class",
    "validate_bucket_token",
]


# ---------------------------------------------------------------------------
# 关系类：恒定 5 值（闭集住 weights.RELATION_CLASSES）
# ---------------------------------------------------------------------------


def relation_class(view: PlayerView, seat: int) -> str:
    """``seat`` 相对**观察者**的关系类（5 值闭集：``self`` / ``lord`` / ``known_ally`` /
    ``known_enemy`` / ``unknown``）。

    判据**不另算一份**：

    - 自己 ⇒ ``self``（座位号，不是身份）；
    - 主公 ⇒ ``lord``（``PlayerPublic.identity`` 是公开的：主公恒亮明）。**主公优先于敌友**：
      对主忠方它是规则级 veto、对反贼它是首要目标，都不是"普通敌人"的价；
    - 未亮明 ⇒ ``unknown``（看不见就是未知，不拿后验装作"已明"）；
    - 已亮明 ⇒ 问 ``heuristic._identity_relation``（**打分路径用的同一个函数**）：
      ``(P敌, P友)`` 精确到 1 ⇒ ``known_enemy`` / ``known_ally``；否则（内奸行的连续 H
      在 0 与 1 之间、或中立席位）⇒ ``unknown``——5 值闭集里没有"已明但中立"的半格，
      加第六档会动摇 A / C 两族的键空间，而"中立"的价本来就该由权重学、不该由分类器拍。

    座位越界 / 人数对不上时**不猜**：``_identity_relation`` 对越界座位返回 ``(0, 0)``，
    本函数因此落 ``unknown``（与 I1 的"算不出就中性"同一条 fail-closed 口径）。
    """
    if seat == view.seat:
        return "self"
    if not 0 <= seat < len(view.players):
        return "unknown"
    player = view.players[seat]
    if player.identity == Identity.LORD:
        return "lord"
    if player.identity is None:
        return "unknown"
    enemy, ally = _relation(view, seat)
    if ally >= 1.0:
        return "known_ally"
    if enemy >= 1.0:
        return "known_enemy"
    return "unknown"


def _relation(view: PlayerView, seat: int) -> tuple[float, float]:
    """``heuristic._identity_relation`` 的**延迟**取用（唯一的敌我判据，不抄第二份）。"""
    from . import heuristic

    return heuristic._identity_relation(view, seat)


# ---------------------------------------------------------------------------
# 局面桶（`ai-train` 票 06；design-04 的 §3）
# ---------------------------------------------------------------------------

BUCKET_VERSION = 1
"""桶元数据的版本（样本 header 的 ``buckets.version``）：边界或 token 语法一改就加一。"""

BUCKET_ALIVE = "alive"
BUCKET_PHASE = "phase"
DEFAULT_BUCKET_SET: tuple[str, ...] = (BUCKET_ALIVE, BUCKET_PHASE)
"""预注册桶集（design-04 的 §3.1）：``alive`` × ``phase`` = 6 桶；``lord`` 留第二层。"""

BUCKET_ALIVE_BOUNDS: tuple[tuple[int, int], ...] = ((1, 2), (3, 4), (5, 5))
"""``alive`` 三档的存活数**闭区间**：≤2 / 3–4 / ≥5（``a0`` / ``a1`` / ``a2`` 的边界原文）。"""

BUCKET_ALIVE_TOKENS: tuple[str, ...] = ("a0", "a1", "a2")
BUCKET_PHASE_TOKENS: tuple[str, ...] = ("p0", "p1")
"""``p0`` = ``QueryKind.PLAY_PHASE``，``p1`` = 其他询问（与打分器同一个枚举换算）。"""


def bucket_dimensions(
    bucket_set: Sequence[str] = DEFAULT_BUCKET_SET,
) -> tuple[str, ...]:
    """校验并规范化桶集：非空、只含预注册维度、无重复、按注册顺序。

    空集 / 未知维度 / 重复 / 顺序不对 ⇒ ``ValueError``（**响亮失败**，不静默给空 token）：
    桶集要写进样本 header 并决定键空间，静默纠正会让"采样的桶"与"读到的桶"对不上。
    """
    if isinstance(bucket_set, str) or not isinstance(bucket_set, (tuple, list)):
        raise ValueError(f"桶集必须是元组 / 列表，得到 {type(bucket_set).__name__}")
    dimensions = tuple(bucket_set)
    if not dimensions:
        raise ValueError("桶集不能为空：没有桶就没有 token（不静默给空 token）")
    registered = (BUCKET_ALIVE, BUCKET_PHASE)
    unknown = [name for name in dimensions if name not in registered]
    if unknown:
        raise ValueError(f"未知桶集维度 {unknown}（只登记了 {registered}；lord 留第二层）")
    if len(set(dimensions)) != len(dimensions):
        raise ValueError(f"桶集维度重复：{dimensions}")
    expected = tuple(name for name in registered if name in dimensions)
    if dimensions != expected:
        raise ValueError(f"桶集必须按注册顺序 {expected}：{dimensions}")
    return dimensions


def bucket(
    query: Query, bucket_set: Sequence[str] = DEFAULT_BUCKET_SET
) -> str:
    """这次询问的局面桶 token（如 ``a1p0``）；只读 ``query.view`` + ``query.kind``。

    同一个询问的**全部候选共享**一个 token（桶是询问级属性）。``alive`` 数公开面的
    ``PlayerPublic.alive``；``phase`` 用与打分器**同一个** ``QueryKind(query.kind)``
    换算（函数内延迟 import ``heuristic``，不新增枚举、不读 ``GameState``）。未知询问
    类型由 ``QueryKind`` 自己响亮失败。
    """
    dimensions = bucket_dimensions(bucket_set)
    pieces: list[str] = []
    for dimension in dimensions:
        if dimension == BUCKET_ALIVE:
            alive = sum(1 for player in query.view.players if player.alive)
            if alive <= BUCKET_ALIVE_BOUNDS[0][1]:
                pieces.append(BUCKET_ALIVE_TOKENS[0])
            elif alive <= BUCKET_ALIVE_BOUNDS[1][1]:
                pieces.append(BUCKET_ALIVE_TOKENS[1])
            else:
                pieces.append(BUCKET_ALIVE_TOKENS[2])
        else:
            from . import heuristic

            kind = heuristic.QueryKind(query.kind)
            pieces.append(
                BUCKET_PHASE_TOKENS[0]
                if kind is heuristic.QueryKind.PLAY_PHASE
                else BUCKET_PHASE_TOKENS[1]
            )
    return "".join(pieces)


def validate_bucket_token(
    token: str, bucket_set: Sequence[str] = DEFAULT_BUCKET_SET
) -> str:
    """核一个桶 token 的形状与取值；合法才返回（否则 ``ValueError``）。

    每维固定 2 字符、按 :func:`bucket_dimensions` 的注册顺序拼接（``a1p0`` / 单维
    ``a1``）。样本 v3 的 ``u`` 与 08a 的读路径共用这一条语法，不各写一份正则。
    """
    dimensions = bucket_dimensions(bucket_set)
    if not isinstance(token, str):
        raise ValueError(f"桶 token 必须是字符串，得到 {type(token).__name__}")
    if len(token) != 2 * len(dimensions):
        raise ValueError(
            f"桶 token 必须是 {2 * len(dimensions)} 个字符（每维 2 个）：{token!r}"
        )
    for offset, dimension in enumerate(dimensions):
        piece = token[2 * offset : 2 * offset + 2]
        allowed = (
            BUCKET_ALIVE_TOKENS if dimension == BUCKET_ALIVE else BUCKET_PHASE_TOKENS
        )
        if piece not in allowed:
            raise ValueError(f"桶 token 的 {dimension} 段不在闭集 {allowed}：{token!r}")
    return token


# ---------------------------------------------------------------------------
# 三族提取
# ---------------------------------------------------------------------------


def _group(pairs: Iterable[tuple[str, float]]) -> dict[str, float]:
    """把 ``(键, 值)`` 摊成"每键一个 float"：**同名累加、保持首次出现序**。

    三族的映射与 :class:`FeatureFrame` 的三个读口共用这一个写法（一处累加口径，不抄五遍）。
    顺序按首次出现（dict 插入序）——确定性纪律要的正是它，故不排序、不用无序容器。
    """
    grouped: dict[str, float] = {}
    for key, value in pairs:
        grouped[key] = grouped.get(key, 0.0) + value
    return grouped


def b_family(items: Sequence[tuple[str, float]]) -> Mapping[str, float]:
    """B 族：把 ``Score`` 的**分项**摊成"每项一个 float"（同名项累加）。

    ``items`` 来自既有的打分器自己（``Score.tracing()`` 下的 ``Scored.items``，票 02），
    本函数只做**改名与归并**：项名 → ``b|<项名>``。值一个都不重算——"B 族不许另算"这条
    因此是结构事实（本模块里没有任何几何 / 概率 / 记牌的实现），不是纪律口号。

    同名项在**多目标**候选上逐目标重复（【南蛮】/【万箭】/ 方天三杀），故这里是累加而不是
    "后一个盖前一个"；**逐项**的口径（含重复、保持 trace 序）在 :class:`FeatureFrame` 里。
    """
    return _group((weights.b_key(item), value) for item, value in items)


def c_family(view: PlayerView) -> Mapping[str, float]:
    """C 族：``c|<事实类>|<关系类>`` 的**计数**（公开事实 × 关系类，机械展开）。

    一条事实按它的**每个**涉及座位计一次（``targets`` 为空时退回 ``actor``——自己的动作
    也进表，落 ``self`` 那一格）。事实类取 ``PublicEvent.fact_class``（机械类别，不是语义）。

    只读**窗口**（``public_history``，≤ 32 条），不读全局长聚合（``public_counts``）：逐事件
    与聚合计数的**单位不同**，混进一张表会互相污染分母（票 07 的两张似然表就是为这个分开
    的）。聚合那一路要进 C 族时，得先把单位说清楚——那是 08b 的选择，本票不先混。
    """
    counts = [
        weights.c_key(event.fact_class, relation_class(view, seat))
        for event in view.public_history
        for seat in _involved_seats(event)
    ]
    return _group((key, 1.0) for key in counts)


def a_family(view: PlayerView) -> Mapping[str, float]:
    """A 族：``a|<技能>|<触发点>|<序>|<事实类>|<关系类>`` 的**计数**。

    ``(技能, 触发点, 序)`` 取 ``PublicEvent.cause`` 的效果编号——它由声明**机械派生**
    （主动技 = 流水线名 + 0，被动技 = ``Hook.timing`` + 同时机内的第几条），故这里不查技能
    目录、不手写效果名表。``cause is None`` 的事实（引擎级事件）**不进 A 族**：它们没有
    技能可归因，归 C 族。

    **A 族完备性**不靠本函数（它对任何事实都是全函数），靠断言：测试从 ``CATALOG`` /
    ``FLOWS`` 派生**每一条流水线的每个叶子步骤**与**每一个 ``Hook.timing``**，核它们的
    效果编号都能经 :func:`weights.a_key` 成一个键、且 :func:`weights.anchor` 有值
    （``tests/test_ai_features.py``）。
    """
    keys = [
        weights.a_key(
            event.cause.skill_id,
            event.cause.trigger,
            event.cause.order,
            event.fact_class,
            relation_class(view, seat),
        )
        for event in view.public_history
        if event.cause is not None
        for seat in _involved_seats(event)
    ]
    return _group((key, 1.0) for key in keys)


def _involved_seats(event: PublicEvent) -> tuple[int, ...]:
    """一条公开事实**涉及**的座位：``targets`` 优先，空则退回 ``actor``，都没有 ⇒ 空元组。

    与 ``PublicFact.record`` 的聚合口径一致（``fact.targets if fact.targets else (None,)``
    的座位那一半）：多目标牌因此逐目标各计一次，"这一发打到了谁"不会被折成一次。
    """
    if event.targets:
        return tuple(event.targets)
    return () if event.actor is None else (int(event.actor),)


# ---------------------------------------------------------------------------
# 一个候选的特征帧
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeatureFrame:
    """一次询问里**一个候选**的三族特征。

    ``items`` 是 B 族的**逐项**序列：保持 ``Score`` 的 trace 序、**同名的重复项各占一行**
    （多目标候选里"期望伤害"逐目标出现）。为什么不像"每项一个 float"那样先归并：浮点加法
    不满足结合律，``((a+b)+c)`` 与 ``(a+c)+b`` 会在末位差 1 ulp，而票面 parity 要的是
    **逐 float 相等**——归并只用于给人看 / 拟合（:meth:`b`），打分走 ``items``。
    """

    items: tuple[tuple[str, float], ...]
    """B 族的 ``(b|<项名>, 值)`` 序列（trace 序、重复保留）。"""

    a: Mapping[str, float]
    """A 族：``a|<技能>|<触发点>|<序>|<事实类>|<关系类>`` → 计数。"""

    c: Mapping[str, float]
    """C 族：``c|<事实类>|<关系类>`` → 计数。"""

    def b(self) -> Mapping[str, float]:
        """B 族"每项一个 float"（同名累加）——拟合与展示读它，打分不读它。"""
        return _group(self.items)

    def position(self) -> Mapping[str, float]:
        """局面项（A + C）的合并映射（A 在前、C 在后，各自按首次出现序）。"""
        return _group((*self.a.items(), *self.c.items()))

    def as_mapping(self) -> Mapping[str, float]:
        """三族合并成一个"键 → 一个 float"的映射（B 归并后在前，局面项在后）。"""
        return _group((*self.b().items(), *self.position().items()))


def frame(query: Query, action: Action) -> FeatureFrame:
    """**一次候选**的三族特征：B 来自既有打分器的 trace，A / C 来自 ``query.view``。

    打分器走 ``heuristic._SCORERS[QueryKind(query.kind)]``——与 ``HeuristicPlayer.respond``
    的**同一条派发**（不另写一份分派，也不在工具侧复算）。打分器返回的是
    :class:`~sgs.agents.heuristic.Scored`（``float`` 子类，带 ``items``）；拿不到分项就是
    "trace 没开或走了非派发路径"，直接炸而不是静默给一条零特征。
    """
    items = _scored_items(query, action)
    view = query.view
    return FeatureFrame(
        items=tuple((weights.b_key(item), value) for item, value in items),
        a=a_family(view),
        c=c_family(view),
    )


def _scored_items(query: Query, action: Action) -> tuple[tuple[str, float], ...]:
    """跑**既有打分器**（trace 打开）并交出它的分项；不是 :class:`Scored` ⇒ ``ValueError``。

    这条守卫在 08a **没有产品调用方**（本票不接线），08b 接进来之后走的就是 :func:`frame`
    这一条路。它的用例因此是一条**合成反例**（本仓钉"守卫接上了线"的固定写法），不是产品
    路径证据；产品路径那一半由上面几条 parity 用例外加 08b 的决策 diff 承担。
    """
    from . import heuristic

    scorer = heuristic._SCORERS[heuristic.QueryKind(query.kind)]
    with heuristic.Score.tracing():
        value = scorer(query, action)
    items = getattr(value, "items", None)
    if items is None:
        raise ValueError(
            f"{query.kind} 的打分器没有交出分项（{type(value).__name__}）：B 族特征只能从"
            "既有的 Score.tracing() 派生，不许在这里另算一份"
        )
    return tuple(items)


def extract(query: Query, action: Action) -> Mapping[str, float]:
    """三族合并的"每项一个 float"映射（:func:`frame` 的扁平视图）。"""
    return frame(query, action).as_mapping()


# ---------------------------------------------------------------------------
# 线性打分：features · weights
# ---------------------------------------------------------------------------


def unweighted_candidate_score(candidate: FeatureFrame) -> float:
    """**不带权**的候选分：``Σ 项值``，逐 float 等于 08a 落地态 / 既有 ``Score`` 总分。

    它只用于**提取层**的等价性判据（项名 / 次序 / 漏项）：08b 起 B 族权重不再是 1.0，
    ``candidate_score`` 已经不等于既有分，而"特征提取得对不对"仍要一条与权重无关的尺子。
    写法刻意与 :func:`candidate_score` 逐字同构（``total += value * 1.0``），故 1 ulp 口径
    也相同——``sum(items)`` 会在嵌套 ``Scored`` 展平后差 1 ulp，不能用。
    """
    total = 0.0
    for _key, value in candidate.items:
        total += value * 1.0
    return total


def candidate_score(candidate: FeatureFrame, mode: Mode = Mode.IDENTITY) -> float:
    """**候选分**：B 族逐项加权求和（``Σ w·f``），按 ``Score`` 的 trace 序累加。

    权重全在锚上时（本票：无任何拟合值）逐 float 等于既有打分器的 ``Score`` 总分——票面
    parity 就是这一条。``value * 1.0`` 在 IEEE 754 下逐位等于 ``value``，故这不是"近似相等"。

    **它能发现什么**：提取层漏了一项 / 改了项名 / 重排了浮点加法 / 派发到别的打分器——
    任何一处都会让和变。**它不能发现什么**：B 族的"定义"就是从既有分项来的（票面要求
    "不许另算"），故这条是**等价性检查**，不是对打分语义的独立复算。独立性由 A / C 的键
    空间断言（关系类闭集 / 效果编号完备性 / 源码字面量全映射）承担。
    """
    total = 0.0
    for key, value in candidate.items:
        total += value * weights.weight(key, mode)
    return total


def position_score(candidate: FeatureFrame, mode: Mode = Mode.IDENTITY) -> float:
    """**局面分**：A + C 族逐键加权求和（同一询问的全部候选上相同 ⇒ 不影响 argmax）。"""
    total = 0.0
    for key, value in candidate.position().items():
        total += value * weights.weight(key, mode)
    return total


def linear(candidate: FeatureFrame, mode: Mode = Mode.IDENTITY) -> float:
    """``features · weights``：候选分 + 局面分（两个和分开算，不重排浮点加法）。"""
    return candidate_score(candidate, mode) + position_score(candidate, mode)
