"""AI 决策级（D 类）确定性用例：**构造局面 + 一次真实询问 + 可接受集合**。

缝与 spec §2.2 / §Testing「A. AI 应答面」逐字一致：

``tests.support.positions.build_state`` → ``Engine.from_state`` → 既有
``run_queries`` 推进到目标询问 → 直接调用**真实** ``HeuristicPlayer.respond(query)``。
**不 mock、不旁路候选**：候选一律取自这次真实询问的 ``legal_actions``；复合动作由真实
引擎校验器裁决（本文件当前的用例没有复合动作）。

判据形状（spec §2.2）：``action ∈ 可接受集合`` **且** ``action ∉ 已知蠢点集合``；
精确相等只在"候选本来唯一"时用。

D 类用例先以 ``xfail(strict=True)`` 落地（spec §2.2「先红后绿」）：strict 会在修好后
变 XPASS、门禁因此判红，逼实现者摘标记，不靠自觉。被拒的条目保留 xfail 并写明原因。
"""

from __future__ import annotations

import pytest

from sgs.agents import HeuristicPlayer
from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Engine, ai_stream
from sgs.state import (
    ActivateSkill,
    Phase,
    SelectCards,
    SelectOption,
    UseCard,
)

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    find_action,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")
"""本模块显式选中的牌堆档位（档位纪律）：不依赖产品默认档。"""

SLASH_ID = cards_of_type(PACK, "slash")[0]
"""主公手里那张【杀】的 ``card_id``（数据表下标，不是牌面对象）。"""

YIJI = "hero.guojia.yiji"

AI_SEAT = 1
"""5 人身份局里 AI 的座位：忠臣。"""

LORD_SEAT = 0
"""主公座位；主公的身份对所有人可见（``PlayerPublic.identity`` / ``_visible_identity``）。"""

ENEMY_SEATS = (2, 3, 4)
"""本构造局面里与主公阵营敌对的三席（两名反贼 + 一名内奸）。AI 看不到这些身份，
但用例以地面真相断言"收牌人不是敌人"——这是 D 类用例允许的构造事实。"""

TIE_SEEDS = range(8)
"""平分抽签的种子样本。

【遗计】收牌人当前的缺陷是打分**平分**（自己与主公同为 ``_SEAT_TARGET_VALUE``），
由 ``ai:<seat>`` 子流抽签决定。单一 seed 只能证明"某一次抽签抽到了谁"，故在固定
若干种子上要求**同一个**性质成立：修好后无论平分如何都该给主公，修好前至少有一个
seed 抽到自己，用例必红。它不是统计断言——输入输出全部确定。"""


class _YijiDriver:
    """把局面推到【遗计】的"交给谁"询问：主公打出【杀】、只发动【遗计】、选牌取一张。"""

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            use = find_action(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == SLASH_ID
                and AI_SEAT in action.targets,
            )
            if use is not None:
                return use
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == YIJI:
            activate = find_action(
                query, lambda action: isinstance(action, ActivateSkill)
            )
            if activate is not None:
                return activate
        if query.kind == "SELECT_CARDS":
            pick = find_action(
                query,
                lambda action: isinstance(action, SelectCards) and len(action.picks) == 1,
            )
            if pick is not None:
                return pick
        return PassPlayer().respond(query)


def _yiji_give_query():
    """构造 5 人局：主公坐 0 号、出牌阶段对 1 号 AI 使用【杀】；AI 是忠臣郭嘉。

    返回**真实引擎停在【遗计】"交给谁"询问上**的 query（尚未应答）。伤害由真实的
    【杀】命中产生，不另压合成 ``DAMAGE`` 帧。
    """
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=5),
        seats=[
            SeatSpec(hp=5, hp_max=5, identity=Identity.LORD, hand=(SLASH_ID,)),
            SeatSpec(
                hp=3, hp_max=3, identity=Identity.LOYALIST, hero="guojia"
            ),
            SeatSpec(hp=4, identity=Identity.REBEL),
            SeatSpec(hp=4, identity=Identity.REBEL),
            SeatSpec(hp=4, identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, SLASH_ID),
        turn_seat=LORD_SEAT,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(
        engine,
        [_YijiDriver() for _ in range(len(state.players))],
        lambda current: current.pending is not None
        and current.pending.kind == "SELECT_OPTION"
        and str(getattr(current.pending, "skill_id", "")).startswith("yiji:give"),
    )
    query = engine.pending
    assert query is not None, "推进器在到达【遗计】收牌人询问前就停了"
    assert query.actor == AI_SEAT, "收牌人询问必须发给受伤的 AI 自己"
    return query


def _yiji_case():
    """夹具 + 由**询问视角**算出的可接受答案与真实候选集。"""
    query = _yiji_give_query()
    offered = {
        action.choice
        for action in query.legal_actions
        if isinstance(action, SelectOption)
    }
    lord = next(
        seat
        for seat, player in enumerate(query.view.players)
        if player.identity is Identity.LORD
    )
    return query, lord, offered


def _assert_case(action, *, acceptable: set[str], stupid: set[str]):
    """D 类判据形状：答案在可接受集合里、且不在已知蠢点里。"""
    assert isinstance(action, SelectOption), f"预期选项动作，实得 {action!r}"
    assert action.choice not in stupid, f"命中已知蠢点：{action.choice!r}"
    assert action.choice in acceptable, f"答案不在可接受集合：{action.choice!r}"


def test_the_yiji_fixture_reaches_a_real_give_query():
    """夹具自检：询问真的由引擎发出、主公答案真的在候选里、AI 应答真的落回候选。

    这条**不带 xfail**：下方 D 用例整体是 strict xfail，若夹具将来被引擎改动弄坏，
    xfail 会把它一起吞掉；自检让"夹具失效 / 应答崩溃"保持可见，而不是静默变绿。
    """
    query, lord, offered = _yiji_case()
    assert query.kind == "SELECT_OPTION"
    assert str(query.skill_id).startswith("yiji:give")
    assert all(f"seat:{seat}" in offered for seat in range(len(query.view.players)))
    assert f"seat:{lord}" in offered
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, AI_SEAT)).respond(query)
        assert isinstance(action, SelectOption), "AI 应答必须落在真实选项面上"
        assert action.choice in offered, "AI 应答必须是真实候选之一"


@pytest.mark.xfail(strict=True, reason="已知蠢点 票 24")
def test_yiji_gives_to_the_revealed_lord_not_self_or_enemy():
    """D3【遗计】收牌人：5 人局 AI 忠臣、主公已亮明 ⇒ 收牌人是主公，不是自己 / 敌人。

    当前实现里"给自己"与"给主公"同分，由平分抽签决定；故这是已知蠢点（票 24 修评分），
    先以 strict xfail 落地。断言不手抄候选：可接受答案由询问视角里的主公身份算出。
    """
    query, lord, offered = _yiji_case()
    acceptable = {f"seat:{lord}"}
    stupid = {
        f"seat:{query.view.seat}",
        *(f"seat:{seat}" for seat in ENEMY_SEATS),
    }
    assert acceptable <= offered, "夹具失效：可接受答案不在真实候选里"

    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, AI_SEAT)).respond(query)
        _assert_case(action, acceptable=acceptable, stupid=stupid)
