"""整数权重的 parity、桶化读路径与橡皮筋（`ai-view` 票 08b / 08c；`ai-train` 票 08a）。

判据都**不依赖仓外产物**（`.scratch/` 是 gitignore 的，门禁不能读它）：

1. **整数权重 vs 浮点参考**：仓里存的是 ``round(w × 1000)``。真正的浮点拟合值落在
   量化格 ``[q − 0.5, q + 0.5] / 1000`` 里，故"两个端点都不改决策"⇒ 格内任何浮点值都不改
   （权重连续变化时 argmax 是分段常值）。这比"和某一组浮点值比"更强，也不需要把浮点表
   复制进仓。
2. **橡皮筋是连续的**（票 08c 的成对目标）：同一把 ``FIT_LAMBDA`` 下，样本 5 对的键被按在
   锚附近，样本多的键自由移动。用一维成对 logistic 的单调梯度求根钉住——不需要 numpy，
   也不需要重跑拟合。
3. **桶化读路径**（`ai-train` 票 08a）：``b|<项名>@<token>`` 的键语法 / 锚 / 回落链
   （桶键 → 基键 → 锚）；``respond`` 每询问只算一次 token、旧表（无 ``@``）不算桶；
   桶权重全 1 时带权候选分与**未加权**决策逐条相同（逐 float 的 parity）；量化格的两端
   在容量表下仍不改任何 argmax。
"""

from __future__ import annotations

import math

import pytest

from sgs.agents import features, weights
from sgs.agents import heuristic as heuristic_scoring
from sgs.agents.weights_data import FIT_LAMBDA, FIT_SAMPLES
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.engine.rng import ai_stream
from sgs.state import QueryKind

PACK = load_content_pack(deck_profile="standard_108")
SEEDS: tuple[int, ...] = (7008, 7009)
"""两局足够：判据是"每条询问上 argmax 相同"，不是统计量。"""

BUCKET_TOKENS: tuple[str, ...] = tuple(
    alive + phase
    for alive in features.BUCKET_ALIVE_TOKENS
    for phase in features.BUCKET_PHASE_TOKENS
)
"""6 个预注册桶 token（``a0p0`` … ``a2p1``）：从 ``features`` 的闭集派生，不手抄一份。"""


def _play_seed(seed: int) -> None:
    """跑一整局 5 人全启发式（与其余 parity 用例同一种批次）。"""
    engine = Engine.new_game(
        seed=seed, rule_config=RuleConfig(player_count=5), pack=PACK, hero_pool=()
    )
    engine.run(
        [heuristic_scoring.HeuristicPlayer(ai_stream(seed, seat)) for seat in range(5)]
    )


def _item_name(key: str) -> str:
    """``b|<项名>``（或桶键）→ 项名：测试侧的反查。

    生产侧**没有**反查口（读口只收项名），这里从特征帧的键还原项名是为了把"整条键"喂给
    量化格端点；键的**拼**法仍只走 :func:`weights.bucket_key` / :func:`weights.b_key`。
    """
    head, sep, rest = key.partition("|")
    assert head == weights.FAMILY_B and sep, key
    return rest.partition(weights.BUCKET_SEP)[0]


def _argmax_set(scores: list[float]) -> frozenset[int]:
    best = max(scores)
    return frozenset(index for index, value in enumerate(scores) if value == best)


def _weight_at(key: str, offset: float, token: str | None = None) -> float:
    """键在量化格里的一个端点：``(q + offset) / 1000``；无拟合值仍是锚。

    键面按 ``token`` 选：给了 token 先读桶键，桶键没有样本回基键（与
    :func:`weights.b_weight_cached` 的回落链同一条）；没有 token 就是旧的基键读法。
    """
    raw = None
    if token is not None:
        raw = weights.SHARED_B.value(weights.bucket_key(_item_name(key), token))
    if raw is None:
        raw = weights.SHARED_B.value(key)
    if raw is None:
        return weights.anchor(key)
    return (raw + offset) / weights.WEIGHT_SCALE


def _production_scores(frames, token: str | None) -> list[float]:
    """生产读口算出的候选分（走 :func:`weights.b_weight_cached`，不另写一份回落链）。"""
    return [
        sum(
            item * weights.b_weight_cached(_item_name(key), token)
            for key, item in frame.items
        )
        for frame in frames
    ]


def _endpoint_scores(frames, token: str | None, offset: float) -> list[float]:
    """量化格端点的候选分（权重换成 ``q + offset``，其余与生产逐字同构）。"""
    return [
        sum(item * _weight_at(key, offset, token) for key, item in frame.items)
        for frame in frames
    ]


def _bucket_table(value_of) -> weights.WeightTable:
    """把仓里 B 表的**项名** × 6 桶铺成一张容量表（行集来自当前 ``SHARED_B``）。

    ``value_of(项名, token) -> 整数刻度`` 决定每个桶的值；``SHARED_B`` 此刻可以是 ai-v14 的
    基键表（本票）或 09 之后的桶表——项名一律从键里反查，不假设表的形态。
    """
    items: dict[str, int] = {}
    for key, value in weights.SHARED_B.rows:
        items.setdefault(_item_name(key), value)
    return weights.WeightTable(
        name="shared_b@capacity-test",
        families=(weights.FAMILY_B,),
        rows=tuple(
            (weights.bucket_key(item, token), int(value_of(item, token)))
            for item in items
            for token in BUCKET_TOKENS
        ),
    )


def _swap_shipped_b(monkeypatch: pytest.MonkeyPatch, table: weights.WeightTable) -> None:
    """临时把主读口的 B 表换掉（缓存必须清：键没变、值变了）。"""
    weights.b_weight_cached.cache_clear()
    monkeypatch.setattr(weights, "SHARED_B", table)


def test_integer_weights_keep_every_decision_of_the_quantization_cell():
    """整数量化**没有改变任何决策**：格的两个端点与生产读数给出同一个 argmax 集合。"""
    if not weights.has_fitted_b():
        pytest.skip("权重表还是空的（08a 落地态）；拟合后这条才有判据意义")

    checked = 0
    mismatches: list[str] = []
    original = heuristic_scoring.HeuristicPlayer.respond

    def capture(player, query):
        if len(query.legal_actions) >= 2:
            actions = list(query.legal_actions)
            frames = [features.frame(query, action) for action in actions]
            token = features.bucket(query) if weights.has_bucketed_b() else None
            production = _production_scores(frames, token)
            expected = _argmax_set(production)
            for offset in (-0.5, 0.5):
                if _argmax_set(_endpoint_scores(frames, token, offset)) != expected:
                    mismatches.append(f"{query.kind}@{query.actor}#{query.seq}")
                    break
        return original(player, query)

    heuristic_scoring.HeuristicPlayer.respond = capture
    try:
        for seed in SEEDS:
            _play_seed(seed)
            checked += 1
    finally:
        heuristic_scoring.HeuristicPlayer.respond = original

    assert checked == len(SEEDS)
    assert not mismatches, "量化格端点改了决策：" + "；".join(mismatches[:5])


def test_integer_weights_keep_every_decision_of_the_quantization_cell_under_buckets(
    monkeypatch: pytest.MonkeyPatch,
):
    """容量表（6 桶、每个桶一组不同数值）下，格的两端仍与生产 argmax 相同。

    这条是"桶化读路径"本身的量化判据：生产读口走 :func:`weights.b_weight_cached` 的
    **桶键**（回落链也在里面），端点按 ``b|<项名>@<token>`` 的整数刻度 ±0.5 取。1 个 token
    读错（例如拿基键的值、或拿别的桶的值）都会让某一端的 argmax 变。
    """
    if not weights.has_fitted_b():
        pytest.skip("权重表还是空的（08a 落地态）；拟合后这条才有判据意义")

    base = {_item_name(key): value for key, value in weights.SHARED_B.rows}
    table = _bucket_table(
        lambda item, token: base[item] + 7 * BUCKET_TOKENS.index(token)
    )
    _swap_shipped_b(monkeypatch, table)
    assert weights.has_bucketed_b() is True

    checked = 0
    mismatches: list[str] = []
    original = heuristic_scoring.HeuristicPlayer.respond

    def capture(player, query):
        if len(query.legal_actions) >= 2:
            actions = list(query.legal_actions)
            frames = [features.frame(query, action) for action in actions]
            token = features.bucket(query)
            production = _production_scores(frames, token)
            expected = _argmax_set(production)
            for offset in (-0.5, 0.5):
                if _argmax_set(_endpoint_scores(frames, token, offset)) != expected:
                    mismatches.append(f"{query.kind}@{query.actor}#{query.seq}")
                    break
        return original(player, query)

    heuristic_scoring.HeuristicPlayer.respond = capture
    try:
        for seed in SEEDS:
            _play_seed(seed)
            checked += 1
    finally:
        heuristic_scoring.HeuristicPlayer.respond = original
        weights.b_weight_cached.cache_clear()

    assert checked == len(SEEDS)
    assert not mismatches, "容量表的量化格端点改了决策：" + "；".join(mismatches[:5])


def _sigmoid(value: float) -> float:
    """一维 logistic（正负两段各自 exp，纯 Python，不 import numpy）。"""
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _pairwise_one_key(sample_scores: list[float], anchor: float, lam: float) -> float:
    """一维成对 logistic 的闭式求根：``min Σ softplus(−y·u) + λ(u)²``（``u = w − anchor``）。

    每个样本是一对"选中候选有这条键、另一候选没有"：``d = 1``、``y = +S``（行为克隆，
    08c 的落仓口径）。梯度 ``Σ −y·σ(−y·u) + 2λu`` 对 ``u`` 单调（目标凸），用二分求根；
    与拟合器的 Hessian 口径（``h = σ(1−σ)·y²``）同一个目标，只是维度为 1、不求数值解。
    """
    low, high = -5.0, 5.0

    def gradient(u: float) -> float:
        total = 2.0 * lam * u
        for y in sample_scores:
            total += -y * _sigmoid(-y * u)
        return total

    for _ in range(200):
        middle = (low + high) / 2.0
        if gradient(middle) > 0.0:
            high = middle
        else:
            low = middle
    return anchor + (low + high) / 2.0


def test_the_rubber_band_keeps_five_samples_near_the_anchor():
    """皮筋**连续**：5 条样本的键仍贴着锚；样本多时数据把键拉走（不是阈值开关）。"""
    if FIT_LAMBDA <= 0 or FIT_SAMPLES <= 0:
        pytest.skip("还没有拟合报告（FIT_LAMBDA / FIT_SAMPLES = 0）")

    anchor = 1.0
    scale = 3.5
    rare = _pairwise_one_key([scale] * 5, anchor, FIT_LAMBDA)
    common = _pairwise_one_key([scale] * 50_000, anchor, FIT_LAMBDA)

    assert abs(rare - anchor) <= 0.1 * anchor, (
        f"5 条样本的键被拉到了 {rare:.3f}（锚 {anchor}）：皮筋没有连续性"
    )
    assert common > anchor * 1.1, f"50000 条样本的键仍被按在 {common:.3f}：λ 盖过了数据"


def test_the_shipped_table_is_integer_millis():
    """仓里的表是**整数刻度**（逐字节可复现的前提），且没有越界的离谱值。"""
    for key, value in weights.SHARED_B.rows:
        assert isinstance(value, int) and not isinstance(value, bool), key
        assert abs(value) <= 10_000, f"{key} = {value} 超出合理量级（±10 倍锚）"


# ---------------------------------------------------------------------------
# 桶化读路径（`ai-train` 票 08a / design-04 的 §7）
# ---------------------------------------------------------------------------


@pytest.fixture
def swapped_b(monkeypatch: pytest.MonkeyPatch):
    """临时换掉主读口的 B 表，并在**前后**清缓存（缓存键不含表：换表必须清）。"""

    def swap(table: weights.WeightTable) -> None:
        weights.b_weight_cached.cache_clear()
        monkeypatch.setattr(weights, "SHARED_B", table)

    yield swap
    weights.b_weight_cached.cache_clear()


def test_the_bucket_key_is_the_base_key_plus_the_token():
    """``bucket_key = b|<项名>@<token>``：``@`` 进键，``|`` 仍是族 / 段的边界。"""
    for token in BUCKET_TOKENS:
        key = weights.bucket_key("斩杀", token)
        assert key == f"b|斩杀@{token}"
        assert weights.family(key) == weights.FAMILY_B
        # 0 样本的桶键读锚 1.0（不是 0）——新桶上线时"没有样本"不等于"这一项不值钱"。
        assert weights.anchor(key) == weights.B_ANCHOR
    # ``b_key`` 仍只拒绝 ``|``：``@`` 是桶键分隔符，基键的语法不因此改变。
    assert weights.b_key("斩杀@x") == "b|斩杀@x"
    assert weights.b_key("斩杀") == "b|斩杀"


def test_a_key_with_a_separator_inside_fails_closed():
    """项名 / token 里出现分隔符 ⇒ 响亮失败（``b|a@b`` 不许有两个意思）。"""
    for bad_item in ("", "斩|杀", "斩@杀"):
        with pytest.raises(ValueError):
            weights.bucket_key(bad_item, "a1p0")
    for bad_token in ("", "a1|p0", "a1@p0", None, 3):
        with pytest.raises(ValueError):
            weights.bucket_key("斩杀", bad_token)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        weights.b_key("斩|杀")


def test_the_read_path_falls_from_bucket_to_base_to_anchor(swapped_b):
    """回落链 **桶键 → 基键 → 锚**：0 样本的桶键回基键，没有基键才回 1.0。"""
    swapped_b(
        weights.WeightTable(
            name="shared_b@fallback",
            families=(weights.FAMILY_B,),
            rows=(
                (weights.bucket_key("项", "a0p0"), 1200),
                (weights.b_key("项"), 1100),
                (weights.b_key("只有基键"), 900),
            ),
        )
    )

    assert weights.has_fitted_b() is True, "桶表也是'有拟合值'，has_fitted_b 的语义不变"
    assert weights.has_bucketed_b() is True
    assert weights.b_weight_cached("项", "a0p0") == 1.2  # 桶键命中
    assert weights.b_weight_cached("项", "a0p1") == 1.1  # 桶缺 ⇒ 基键（不是锚）
    assert weights.b_weight_cached("项") == 1.1  # 旧版未桶化读法
    assert weights.b_weight_cached("只有基键", "a0p0") == 0.9
    assert weights.b_weight_cached("没见过", "a0p0") == weights.B_ANCHOR
    assert weights.b_weight_cached("没见过") == weights.B_ANCHOR
    # 整条桶键也走 ``weight``（拟合 / 报表侧的读口），不另算。
    assert weights.weight(weights.bucket_key("项", "a0p0")) == 1.2
    assert weights.weight(weights.bucket_key("项", "a2p1")) == weights.B_ANCHOR


def test_has_bucketed_b_is_true_exactly_when_a_row_carries_the_separator(swapped_b):
    """``has_bucketed_b`` 只看**表里的行**：旧冻结表 / 空表 ⇒ False，有 ``@`` 行 ⇒ True。"""
    swapped_b(
        weights.WeightTable(
            name="t", families=(weights.FAMILY_B,), rows=((weights.b_key("项"), 1100),)
        )
    )
    assert weights.has_fitted_b() is True
    assert weights.has_bucketed_b() is False

    swapped_b(
        weights.WeightTable(
            name="t",
            families=(weights.FAMILY_B,),
            rows=((weights.bucket_key("项", "a0p0"), 1100),),
        )
    )
    assert weights.has_bucketed_b() is True

    swapped_b(weights.WeightTable(name="t", families=(weights.FAMILY_B,)))
    assert weights.has_fitted_b() is False
    assert weights.has_bucketed_b() is False


def test_the_shipped_table_has_no_bucket_rows_yet():
    """本票交付时仓里的表仍是 ai-v14（69 行基键）⇒ 默认路径走基键读法、行为不变。

    09 落容量表（ai-v15）时这条会翻红：按新表把它改成 ``is True``，并重跑上面两条量化格
    用例——判据本身不随表形态变。
    """
    assert weights.has_bucketed_b() is False
    assert [key for key in weights.SHARED_B.keys() if weights.BUCKET_SEP in key] == []


def test_the_version_local_policy_reads_bucket_rows_too():
    """旧版本代理（`ai-view` 票 15）也认桶键：``has_bucketed_b`` + ``b_weight_cached(token)``。"""
    policy = weights.local_policy(
        (
            (weights.bucket_key("项", "a0p0"), 1200),
            (weights.b_key("项"), 1100),
            (weights.b_key("只有基键"), 900),
        ),
        label="capacity-probe",
    )

    assert policy.has_fitted_b() is True
    assert policy.has_bucketed_b() is True
    assert policy.b_weight_cached("项", "a0p0") == 1.2
    assert policy.b_weight_cached("项", "a0p1") == 1.1
    assert policy.b_weight_cached("项") == 1.1
    assert policy.b_weight_cached("只有基键", "a2p1") == 0.9
    assert policy.b_weight_cached("没见过", "a0p0") == weights.B_ANCHOR
    # 纯代码面（键语法）由代理原样转发当前模块：键的拼法不随版本冻结。
    assert policy.bucket_key("项", "a0p0") == "b|项@a0p0"

    # 旧版本冻结表（ai-v13 / ai-v14）：没有 ``@`` ⇒ 不桶化；旧版 ``respond`` 不传 token。
    old = weights.local_policy(((weights.b_key("项"), 1100),), label="ai-v14")
    assert old.has_fitted_b() is True
    assert old.has_bucketed_b() is False
    assert old.b_weight_cached("项") == 1.1
    assert old.b_weight_cached("项", "a0p0") == 1.1  # 桶缺 ⇒ 基键，不归零

    empty = weights.local_policy(())
    assert empty.has_fitted_b() is False
    assert empty.has_bucketed_b() is False
    assert empty.b_weight_cached("项") == weights.B_ANCHOR


def test_respond_computes_one_bucket_token_per_query(
    monkeypatch: pytest.MonkeyPatch, swapped_b
):
    """桶表下：**每个询问恰好算一次** ``features.bucket``（桶是询问级属性，不逐候选算）。"""
    swapped_b(_bucket_table(lambda _item, _token: 1000))
    queries: list[tuple[int, int]] = []
    bucket_calls: list[tuple[int, int]] = []
    original_bucket = features.bucket
    original = heuristic_scoring.HeuristicPlayer.respond

    def counting_bucket(query):
        bucket_calls.append((query.actor, query.seq))
        return original_bucket(query)

    def recording_respond(player, query):
        queries.append((query.actor, query.seq))
        return original(player, query)

    monkeypatch.setattr(features, "bucket", counting_bucket)
    monkeypatch.setattr(heuristic_scoring.HeuristicPlayer, "respond", recording_respond)

    _play_seed(SEEDS[0])

    assert len(queries) >= 100, f"只跑到 {len(queries)} 个询问；批次可能没接上"
    assert bucket_calls == queries, "桶 token 不是每询问恰好算一次"


def test_the_old_table_never_computes_a_bucket_token(
    monkeypatch: pytest.MonkeyPatch, swapped_b
):
    """无桶表（ai-v13 / ai-v14 的基键表）⇒ ``respond`` 不调桶函数：旧版行为逐字节不变。"""
    swapped_b(
        weights.WeightTable(
            name="shared_b@ai-v14-shape",
            families=(weights.FAMILY_B,),
            rows=((weights.b_key("斩杀"), 1002),),
        )
    )
    assert weights.has_fitted_b() is True
    assert weights.has_bucketed_b() is False

    def explode(_query):
        raise AssertionError("无桶表不该调用 features.bucket")

    monkeypatch.setattr(features, "bucket", explode)

    _play_seed(SEEDS[0])  # 跑完 = 一次都没调


def test_an_empty_table_never_computes_a_bucket_token(
    monkeypatch: pytest.MonkeyPatch, swapped_b
):
    """空表（``has_fitted_b`` False）⇒ 连带权路径都不走，更不调桶函数。"""
    swapped_b(weights.WeightTable(name="empty", families=(weights.FAMILY_B,)))

    def explode(_query):
        raise AssertionError("空表不该调用 features.bucket")

    monkeypatch.setattr(features, "bucket", explode)

    _play_seed(SEEDS[0])


def test_all_one_bucket_weights_are_the_unweighted_decisions(swapped_b):
    """桶权重**全 1** ⇒ 带权候选分逐 float 等于原 ``Score`` 总分，决策逐条相同。

    基线是**未加权**路径（``has_fitted_b`` 压成 False），不是 ai-v14 的拟合值：票面要的是
    "权重全 1 = 不改分数"。逐 float 的等式在 ``respond`` 同一条读口上核（每候选两份分数），
    决策序列再单独比一次（RNG 消耗相同才算真的没改）。
    """
    baseline: list[str] = []
    original = heuristic_scoring.HeuristicPlayer.respond

    def baseline_respond(player, query):
        action = original(player, query)
        baseline.append(repr(action))
        return action

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(weights, "has_fitted_b", lambda: False)
        patch.setattr(heuristic_scoring.HeuristicPlayer, "respond", baseline_respond)
        _play_seed(SEEDS[0])

    swapped_b(_bucket_table(lambda _item, _token: 1000))
    weighted: list[str] = []
    mismatches: list[str] = []

    def weighted_respond(player, query):
        token = features.bucket(query)
        scorer = heuristic_scoring._SCORERS[QueryKind(query.kind)]
        for action in query.legal_actions:
            with heuristic_scoring.Score.tracing():
                bare = float(scorer(query, action))
            got = heuristic_scoring._weighted_candidate_score(
                scorer, query, action, token
            )
            if got != bare:
                mismatches.append(
                    f"{query.kind}@{query.actor}#{query.seq}: {got!r} != {bare!r}"
                )
        action = original(player, query)
        weighted.append(repr(action))
        return action

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(heuristic_scoring.HeuristicPlayer, "respond", weighted_respond)
        _play_seed(SEEDS[0])

    assert len(baseline) >= 100, f"基线只跑到 {len(baseline)} 个询问"
    assert not mismatches, "全 1 桶权重的候选分与原分不等：" + "；".join(mismatches[:5])
    assert weighted == baseline, "全 1 桶权重改了决策序列"
