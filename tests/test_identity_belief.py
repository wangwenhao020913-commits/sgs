"""身份判定与身份信念（`ai-view` 票 07；spec §2.6）。

判据（票面 `## 验收`）：

1. **后验自洽**：每个询问上 ``Σ 分配质量 == 1``；已亮明 / 已阵亡座位的边缘恒 0/1。
2. **枚举规模**：4/5/6/7/8 人的有效联合分配 = 6/12/20/60/105（票面数字）。
3. **行为证据有方向**：对主公出手的未知座位，其 ``P(反贼)`` 上升；濒死窗口的放弃按
   **濒死座位**归类。
4. **干预折扣**：我先打、对方反击的位移**显著小于**同形但无先手的事件。
5. **无证据 / PvE**：退回 I1 的均匀后验 / 中性 ``(0, 0)``（逐值不变）。
6. **不泄露**：隐藏手牌变化不动后验（只看公开面）。
7. **确定性**：跨 ``PYTHONHASHSEED`` 逐字节一致。
8. **表的形状 / 行和**：3 行为 × 2 先手方 × 4×4 身份对，每个 (发起, 目标, 先手方) 的
   三个行为概率求和为 1；敌意牌型表与启发式侧的真源不漂。

测试用的是**构造视图**（不跑引擎）：本票的读口是纯函数，构造视图能精确摆出"谁打了谁"，
比实机更容易钉住边界。实机路径的尺子在 `tests/test_ai_eval_replay.py`（``--score`` 的行为
后验行）与票面的判决批读数里。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sgs.agents import identity
from sgs.agents import heuristic as heuristic_scoring
from sgs.data import IDENTITY_ROSTERS, Identity, load_content_pack
from sgs.state import (
    CardRef,
    FactKind,
    HpChangeDelta,
    Pass,
    Phase,
    PlayerPublic,
    PlayerView,
    PublicCount,
    PublicEvent,
    UseCard,
)

ROOT = Path(__file__).resolve().parent.parent
PACK = load_content_pack(deck_profile="standard_ex_160")


# ---------------------------------------------------------------------------
# 构造视图（不跑引擎：只摆公开面）
# ---------------------------------------------------------------------------


def _player(
    seat: int,
    identity_value: Identity | None,
    *,
    alive: bool = True,
    hp: int = 4,
) -> PlayerPublic:
    return PlayerPublic(
        seat=seat,
        hp=hp if alive else 0,
        hp_max=4,
        hand_count=2,
        alive=alive,
        equip=(),
        judge=(),
        identity=identity_value,
    )


def _use(
    seq: int,
    actor: int,
    targets: tuple[int, ...],
    *,
    card_type: str = "slash",
) -> PublicEvent:
    return PublicEvent(
        seq=seq,
        kind=FactKind.ANSWER,
        fact_class="answer:UseCard",
        actor=actor,
        targets=targets,
        window="PLAY_PHASE",
        payload=UseCard(
            answers_seq=seq,
            card=CardRef(card_id=seq, type=card_type, name=card_type),
            targets=targets,
        ),
    )


def _hp(seq: int, actor: int, target: int, amount: int) -> PublicEvent:
    before = 4
    return PublicEvent(
        seq=seq,
        kind=FactKind.DELTA,
        fact_class="delta:hp_change",
        actor=actor,
        targets=(target,),
        window=None,
        payload=HpChangeDelta(
            seat=target, before=before, after=before + amount, amount=amount
        ),
    )


def _pass_dying(seq: int, actor: int) -> PublicEvent:
    return PublicEvent(
        seq=seq,
        kind=FactKind.ANSWER,
        fact_class="answer:Pass",
        actor=actor,
        targets=(),
        window="PEACH",
        payload=Pass(answers_seq=seq),
    )


def _view(
    identities: tuple[Identity | None, ...],
    *,
    viewer: int,
    alive: tuple[bool, ...] | None = None,
    history: tuple[PublicEvent, ...] = (),
    self_hand: tuple[CardRef, ...] = (),
    counts: tuple[PublicCount, ...] = (),
) -> PlayerView:
    alive = alive or tuple(True for _ in identities)
    return PlayerView(
        seat=viewer,
        players=tuple(
            _player(seat, value, alive=alive[seat])
            for seat, value in enumerate(identities)
        ),
        self_hand=self_hand,
        deck_count=60,
        discard=(),
        processing=(),
        turn_seat=0,
        phase=Phase.PLAY,
        public_history=history,
        public_counts=counts,
    )


# ---------------------------------------------------------------------------
# 1. 枚举规模与后验自洽
# ---------------------------------------------------------------------------


def test_the_joint_assignments_match_the_ticket_counts():
    """4/5/6/7/8 人 = 6/12/20/60/105 种（主公一席已明 ⇒ 剩下 n−1 席的多重集排列）。"""
    expected = {4: 6, 5: 12, 6: 20, 7: 60, 8: 105}
    for players, count in expected.items():
        roster = IDENTITY_ROSTERS[players]
        remaining = tuple(identity_value for identity_value in roster[1:])
        assignments = identity._multiset_assignments(remaining)
        assert len(assignments) == count, f"{players} 人局的分配数不是 {count}"


def test_the_posterior_is_normalized_and_keeps_revealed_seats_exact():
    """``Σ 分配质量 == 1``；已亮明 / 已阵亡座位的边缘恒 0/1。"""
    identities = (
        Identity.REBEL,      # 0 = 观察者（反贼，自己可见）
        Identity.LORD,       # 1 = 主公（恒亮明）
        None,                # 2 = 未知
        None,                # 3 = 未知
        Identity.REBEL,      # 4 = 已阵亡（阵亡即亮明）
    )
    history = (
        _use(1, 2, (1,)),    # 2 打主公
        _use(2, 3, (1,)),    # 3 打主公
        _use(3, 2, (0,)),    # 2 打发观察者
    )
    view = _view(identities, viewer=0, alive=(True, True, True, True, False), history=history)
    posterior = identity.joint_posterior(view)

    assert posterior is not None
    assert math.isclose(sum(posterior.weights), 1.0, abs_tol=1e-12)
    for seat, value in ((0, Identity.REBEL), (1, Identity.LORD), (4, Identity.REBEL)):
        row = posterior.marginal(seat)
        assert row[identity.IDENTITY_INDEX[value]] == 1.0, f"座位 {seat} 的已明身份不是 1"
        assert sum(row) == pytest.approx(1.0), f"座位 {seat} 的边缘不是分布"
    # 未知座位仍是一条分布（两个未知 + 两个反贼身份在分配里）
    for seat in (2, 3):
        row = posterior.marginal(seat)
        assert sum(row) == pytest.approx(1.0)
        assert all(0.0 <= value <= 1.0 for value in row)


# ---------------------------------------------------------------------------
# 2. 行为证据有方向
# ---------------------------------------------------------------------------


def test_a_hostile_act_against_the_lord_moves_the_actor_toward_rebel():
    """对主公出手的未知座位：反贼的概率高于配比均匀后验。

    5 人局、观察者 = 忠臣（1 号）、主公 = 0 号 ⇒ 剩余 = 反 / 反 / 内；均匀后验给
    ``P(反) = 2/3``。表里 ``harm`` 打在主公身上时，反贼比内奸常见 ⇒ 后验必须上移。
    """
    identities = (
        Identity.LORD, Identity.LOYALIST, None, None, None
    )
    history = (_use(1, 2, (0,)),)  # 座位 2 对主公出【杀】
    view = _view(identities, viewer=1, history=history)
    posterior = identity.joint_posterior(view)
    assert posterior is not None

    row = posterior.marginal(2)
    rebel = row[identity.IDENTITY_INDEX["rebel"]]
    renegade = row[identity.IDENTITY_INDEX["renegade"]]
    assert rebel > 2 / 3, f"打主公的反贼概率没有上升：{row}"
    assert rebel > renegade, f"打主公的座位更像反贼而不是内奸：{row}"


def test_a_dying_pass_is_attributed_to_the_dying_seat():
    """濒死求桃窗口的 ``Pass`` 没有目标，濒死者从更早的 ``hp_change`` 读回。"""
    identities = (
        Identity.LORD, Identity.LOYALIST, None, None, None
    )
    history = (
        _hp(1, 2, 0, -4),        # 2 把主公打到 0
        _pass_dying(2, 3),       # 3 在主公的濒死窗口放弃
        _pass_dying(3, 1),       # 观察者自己也放弃（不收：自己发起的事实）
    )
    view = _view(identities, viewer=1, history=history)
    observations = identity.behavior_observations(view)

    assert [o for o in observations if o.actor == 3] == [
        identity.ObservedBehavior(
            seq=2, actor=3, target=0, behavior="pass_dying_lord", context="first"
        )
    ]


def test_pressure_observations_read_the_public_counts():
    """``public_counts`` 的拿牌 / 弃牌格子摊成长期压力观测；自己发起的 / 自己弃自己的不收。"""
    identities = (
        Identity.LORD, Identity.LOYALIST, None, None, None
    )
    counts = (
        PublicCount(owner=2, seat=0, kind="move:LOSE", count=2),
        PublicCount(owner=2, seat=0, kind="move:DISCARD", count=1),
        PublicCount(owner=3, seat=1, kind="move:DISCARD", count=1),
        PublicCount(owner=1, seat=2, kind="move:LOSE", count=4),   # 自己发起（观察者）
        PublicCount(owner=2, seat=2, kind="move:LOSE", count=9),   # 自己弃自己
        PublicCount(owner=2, seat=0, kind="delta:hp_change", count=5),  # 不是压力类
    )
    view = _view(identities, viewer=1, counts=counts)

    assert identity.pressure_observations(view) == (
        identity.PressureObservation(actor=2, behavior="pressure_lord", count=3),
        identity.PressureObservation(actor=3, behavior="pressure_me", count=1),
    )


def test_pressure_evidence_moves_the_posterior_without_a_window():
    """窗口里没有行为事实、但 ``public_counts`` 有长期压力 ⇒ 后验仍能读出来。"""
    identities = (
        Identity.LORD, Identity.LOYALIST, None, None, None
    )
    counts = (
        PublicCount(owner=2, seat=0, kind="move:LOSE", count=2),
        PublicCount(owner=2, seat=0, kind="move:DISCARD", count=1),
    )
    view = _view(identities, viewer=1, counts=counts)
    posterior = identity.joint_posterior(view)

    assert posterior is not None, "长期压力证据没有被读进后验"
    row = posterior.marginal(2)
    assert row[identity.IDENTITY_INDEX["rebel"]] > 2 / 3


# ---------------------------------------------------------------------------
# 3. 干预折扣
# ---------------------------------------------------------------------------


def _counterattack_view(*, with_prior_strike: bool) -> PlayerView:
    """观察者（忠臣）被座位 2 打；``with_prior_strike`` = 观察者先打过座位 2。"""
    identities = (
        Identity.LORD, Identity.LOYALIST, None, None, None
    )
    history = []
    if with_prior_strike:
        history.append(_use(1, 1, (2,)))  # 我先打座位 2
    history.append(_use(2, 2, (1,)))      # 座位 2 反击我
    return _view(identities, viewer=1, history=tuple(history))


def test_intervention_discount_shrinks_a_counterattack_shift():
    """我先打、对方反击 ⇒ 后验位移显著小于同形但无先手的事件。"""
    plain = _counterattack_view(with_prior_strike=False)
    retaliation = _counterattack_view(with_prior_strike=True)

    plain_posterior = identity.joint_posterior(plain)
    retaliation_posterior = identity.joint_posterior(retaliation)
    assert plain_posterior is not None
    assert retaliation_posterior is not None

    observations = identity.behavior_observations(retaliation)
    # 观察者自己发起的事实不进观测（知道自己的身份），故只有反击那一条。
    assert [o.viewer_initiated for o in observations] == [True], (
        "反击那一发必须盖 '我先动手' 的戳"
    )
    assert [o.context for o in observations] == ["responder"]

    uniform = identity.marginals(
        _view((Identity.LORD, Identity.LOYALIST, None, None, None), viewer=1)
    )[2]

    def shift(posterior: identity.Posterior) -> float:
        row = posterior.marginal(2)
        return 0.5 * sum(abs(a - b) for a, b in zip(row, uniform, strict=True))

    plain_shift = shift(plain_posterior)
    discounted = shift(retaliation_posterior)
    assert plain_shift > 0.0
    assert discounted < plain_shift * 0.75, (
        f"干预折扣没有显著压小位移：有先手 {discounted:.4f} vs 无先手 {plain_shift:.4f}"
    )


# ---------------------------------------------------------------------------
# 4. 无证据 / 不泄露 / 确定性
# ---------------------------------------------------------------------------


def test_no_evidence_falls_back_to_the_uniform_posterior():
    """没有公开行为证据 ⇒ ``joint_posterior`` 返回 ``None``，关系读数逐值等于 I1。"""
    identities = (
        Identity.LORD, Identity.LOYALIST, None, None, None
    )
    view = _view(identities, viewer=1)

    assert identity.joint_posterior(view) is None
    row = identity.marginals(view)[2]
    assert row[0] == 0.0 and row[1] == 0.0
    assert row[2] == pytest.approx(2 / 3)
    assert row[3] == pytest.approx(1 / 3)
    assert heuristic_scoring._identity_relation(view, 2) == (1.0, 0.0), (
        "忠臣眼里剩余全是敌人（2 反 + 1 内）⇒ P(敌)=1"
    )


def test_hidden_hands_do_not_move_the_posterior():
    """只改隐藏手牌 ⇒ 后验与关系读数逐字节不变（输入只有公开面）。"""
    identities = (
        Identity.LORD, Identity.LOYALIST, None, None, None
    )
    history = (_use(1, 2, (0,)),)
    hand_a = (CardRef(card_id=1, type="slash", name="杀"),)
    hand_b = (CardRef(card_id=2, type="dodge", name="闪"),)
    view_a = _view(identities, viewer=1, history=history, self_hand=hand_a)
    view_b = _view(identities, viewer=1, history=history, self_hand=hand_b)

    assert identity.marginals(view_a) == identity.marginals(view_b)
    assert heuristic_scoring._identity_relation(view_a, 2) == heuristic_scoring._identity_relation(
        view_b, 2
    )


def test_determinism_across_python_hash_seeds():
    """跨 ``PYTHONHASHSEED`` 逐字节一致（同输入同输出）。"""
    script = """
import json
from sgs.agents import identity
from sgs.data import Identity
from sgs.state import CardRef, FactKind, Pass, Phase, PlayerPublic, PlayerView, PublicEvent, UseCard

def use(seq, actor, targets):
    return PublicEvent(seq=seq, kind=FactKind.ANSWER, fact_class="answer:UseCard",
        actor=actor, targets=targets, window="PLAY_PHASE",
        payload=UseCard(answers_seq=seq, card=CardRef(card_id=seq, type="slash", name="slash"),
                        targets=targets))

players = tuple(PlayerPublic(seat=i, hp=4, hp_max=4, hand_count=2, alive=True,
    equip=(), judge=(), identity=v) for i, v in enumerate(
        (Identity.LORD, Identity.LOYALIST, None, None, None)))
view = PlayerView(seat=1, players=players, self_hand=(), deck_count=60, discard=(),
    processing=(), turn_seat=0, phase=Phase.PLAY,
    public_history=(use(1, 2, (0,)), use(2, 3, (0,)), use(3, 2, (1,))))
rows = identity.marginals(view)
print(json.dumps([[round(v, 12) for v in row] for row in rows], sort_keys=True))
"""
    outputs = []
    for seed in ("0", "1"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.append(completed.stdout)
    assert outputs[0] == outputs[1], "跨 PYTHONHASHSEED 的后验读数不一致"


# ---------------------------------------------------------------------------
# 5. 表的形状 / 真源不漂 / 判定出口
# ---------------------------------------------------------------------------


def test_the_likelihood_table_is_a_conditional_distribution():
    """每个 ``(发起, 观察者, 先手方)`` 的九个行为概率求和为 1；全部为正（平滑）。"""
    assert identity.BEHAVIOR_CLASSES == (
        "harm_lord",
        "harm_me",
        "harm_other",
        "help_lord",
        "help_me",
        "help_other",
        "pass_dying_lord",
        "pass_dying_me",
        "pass_dying_other",
    )
    order = identity.IDENTITY_ORDER
    for context in identity.CONTEXTS:
        for viewer in order:
            for actor_index in range(4):
                total = 0.0
                for behavior in identity.BEHAVIOR_CLASSES:
                    row = identity.BEHAVIOR_LIKELIHOODS[
                        (behavior, context, viewer.value)
                    ]
                    value = row[actor_index]
                    assert 0.0 < value < 1.0, (
                        behavior, context, viewer, actor_index
                    )
                    total += value
                assert total == pytest.approx(1.0, abs=1e-4), (
                    f"{order[actor_index]} / 观察者 {viewer} / {context} 的行和不是 1："
                    f"{total}"
                )


def test_the_harmful_card_set_matches_the_heuristic_source():
    """敌意牌型的闭集与启发式侧的真源不漂（本模块不 import heuristic，靠测试双向核）。"""
    slash_types = {
        card.type for card in PACK.deck.cards if heuristic_scoring._is_slash_card(card)
    }
    assert identity.HARMFUL_CARD_TYPES == (
        set(heuristic_scoring._NULLIFY_HARMFUL_TRICKS) | slash_types
    )
    assert identity.HELPFUL_CARD_TYPES == {"peach", "peach_garden"}


def test_the_identity_judgment_reads_the_same_marginal():
    """判定出口 = 边缘后验的最大项 + 它的概率（不进决策）。"""
    identities = (
        Identity.LORD, Identity.LOYALIST, None, None, None
    )
    view = _view(identities, viewer=1, history=(_use(1, 2, (0,)),))
    judgment = identity.identity_judgment(view, 2)
    row = identity.marginals(view)[2]

    assert judgment is not None
    assert judgment.label is Identity.REBEL
    assert judgment.confidence == row[identity.IDENTITY_INDEX["rebel"]]
    assert judgment.marginal == row


def test_the_relation_label_keeps_the_tie_cases():
    """敌 / 友 / 中立 / 未定 的闭集与平票口径。"""
    assert identity.relation_label(0.9, 0.05) == "敌"
    assert identity.relation_label(0.05, 0.9) == "友"
    assert identity.relation_label(0.2, 0.2) == "中立"
    assert identity.relation_label(0.5, 0.5) == "未定"
    assert identity.relation_confidence(0.9, 0.05) == pytest.approx(0.9)
