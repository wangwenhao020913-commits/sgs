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
    Pass,
    Phase,
    SelectCards,
    SelectOption,
    UseCard,
    WindowCause,
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


# ---------------------------------------------------------------------------
# D1【无懈可击】目标化（票 18）：只在该宣时宣
# ---------------------------------------------------------------------------

NULLIFY = "nullify"
"""无懈窗口里唯一合法的响应牌名（与 ``RespondQuery.required`` 同值）。

用例把它当**牌面事实**读（同 ``test_nullify.py`` 的姿态），不抄任何候选生成逻辑。
"""


class _TrickDriver:
    """出牌者：在 ``user`` 的出牌阶段使用指定实体牌；其余询问取最小合法应答。

    ``declare_at``（票 18 的嵌套用例）让某个座位在**无懈窗口**里声明一张【无懈可击】，
    且只声明一次——声明由引擎收齐后只有优先级最高者实际使用，故它能在处理区里造出
    "无懈正在结算"的状态，而这一层嵌套窗口正好是那条用例要问的时机。
    """

    def __init__(
        self,
        *,
        card_id: int,
        targets: tuple[int, ...],
        user: int,
        declare_at: int | None = None,
    ) -> None:
        self._card_id = card_id
        self._targets = targets
        self._user = user
        self._declare_at = declare_at
        self._played = False

    def respond(self, query):
        if query.kind == "PLAY_PHASE" and query.actor == self._user and not self._played:
            use = find_action(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == self._card_id
                and action.targets == self._targets,
            )
            if use is not None:
                self._played = True
                return use
        if self._declare_at is not None and _is_nullify_window(query):
            if query.actor == self._declare_at:
                declare = find_action(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.type == NULLIFY,
                )
                if declare is not None:
                    self._declare_at = None
                    return declare
        return PassPlayer().respond(query)


def _is_nullify_window(query) -> bool:
    """这条询问是不是无懈窗口（``RESPOND`` + ``cause = NULLIFY``）。"""
    return (
        getattr(query, "kind", None) == "RESPOND"
        and getattr(query, "cause", None) is WindowCause.NULLIFY
    )


def _nullify_query(
    *,
    trick: str,
    user: int,
    targets: tuple[int, ...],
    identities: tuple[Identity, ...],
    ai_seat: int,
    hp: dict[int, int] | None = None,
    declare_at: int | None = None,
    nested: bool = False,
):
    """把局面推到 ``ai_seat`` 的一条真实无懈询问。

    - ``user`` 在出牌阶段使用 ``trick``（目标 ``targets``）；
    - AI 座位手里恒有【无懈可击】，故"宣 / 不宣"是真的选择（不是合法性碰巧不成立）；
    - ``nested=True`` 时另让 ``declare_at`` 先声明一张无懈，于是停下时**处理区顶牌是
      无懈本身**——嵌套窗口靠这个状态与第一层区分。

    断言只到"引擎真的停在 AI 的这条询问上"；判据由各用例给。
    """
    trick_id = cards_of_type(PACK, trick)[0]
    hands: dict[int, tuple[int, ...]] = {user: (trick_id,)}
    used = {trick_id}
    pool = cards_of_type(PACK, NULLIFY)
    assert len(pool) >= 4, "无懈档位变了：两条手牌各 2 张的构造需要至少 4 张"
    seats_with_nullify = [ai_seat]
    if nested:
        assert declare_at is not None, "嵌套用例必须指定先声明无懈的座位"
        seats_with_nullify.insert(0, declare_at)
    cursor = 0
    for seat in seats_with_nullify:
        cards = tuple(pool[cursor : cursor + 2])
        cursor += 2
        hands[seat] = hands.get(seat, ()) + cards
        used.update(cards)
    hp_overrides = dict(hp or {})
    seats = [
        SeatSpec(
            hp=hp_overrides.get(seat, 4),
            hp_max=4,
            identity=identity,
            hand=hands.get(seat, ()),
        )
        for seat, identity in enumerate(identities)
    ]
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=len(identities)),
        seats=seats,
        deck=tuple(
            card_id
            for card_id in range(len(PACK.deck.cards))
            if card_id not in used
        ),
        turn_seat=user,
        phase=None,
    )
    engine = Engine.from_state(state, seed=7)
    driver = _TrickDriver(
        card_id=trick_id, targets=targets, user=user, declare_at=declare_at
    )
    run_queries(
        engine,
        [driver for _ in identities],
        lambda current: _reached_nullify(
            current.pending, ai_seat=ai_seat, nested=nested
        ),
    )
    query = engine.pending
    assert query is not None and query.actor == ai_seat, "推进器没有停在 AI 的无懈询问上"
    return query


def _reached_nullify(query, *, ai_seat: int, nested: bool) -> bool:
    """停止条件：AI 的无懈询问；嵌套用例还要等处理区顶牌变成无懈。"""
    if query is None or not _is_nullify_window(query) or query.actor != ai_seat:
        return False
    if not nested:
        return True
    return bool(query.view.processing) and query.view.processing[-1].type == NULLIFY


def _assert_nullify_answer(query, *, declare: bool) -> None:
    """D1 判据：该宣 ⇒ 真的用一张【无懈可击】；不该宣 ⇒ ``Pass``。

    判据只在"宣 / 不宣"这一层（多张无懈里挑哪张没有语义差异）；跑满 #TIE_SEEDS
    是为了让"用 0 分与 Pass 打平"的实现不能靠平分抽签撞过用例。
    """
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        if declare:
            assert isinstance(action, UseCard), f"预期使用【无懈可击】，实得 {action!r}"
            assert action.card.type == NULLIFY, "无懈窗口里只该使用【无懈可击】"
        else:
            assert isinstance(action, Pass), f"预期 Pass，实得 {action!r}"


def _own_ex_nihilo_query():
    """自己【无中生有】被问"要不要无懈"：AI = 回合角色（座位 0，主公）。"""
    return _nullify_query(
        trick="ex_nihilo",
        user=0,
        targets=(0,),
        identities=(Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
        ai_seat=0,
    )


def _enemy_ex_nihilo_query():
    """敌人【无中生有】：AI 是反贼（座位 1），主公（座位 0）是**已亮明**的敌人。"""
    return _nullify_query(
        trick="ex_nihilo",
        user=0,
        targets=(0,),
        identities=(Identity.LORD, Identity.REBEL, Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE),
        ai_seat=1,
    )


def _ally_duel_query():
    """盟友（主公）被【决斗】：AI 是忠臣（座位 1），座位 2 用【决斗】打主公。"""
    return _nullify_query(
        trick="duel",
        user=2,
        targets=(0,),
        identities=(Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
        ai_seat=1,
    )


def _enemy_indulgence_query():
    """敌人（主公）被【乐不思蜀】：AI 是反贼（座位 1），座位 2 把乐贴在主公头上。"""
    return _nullify_query(
        trick="indulgence",
        user=2,
        targets=(0,),
        identities=(Identity.LORD, Identity.REBEL, Identity.REBEL, Identity.LOYALIST, Identity.RENEGADE),
        ai_seat=1,
    )


def _enemy_peach_garden_query():
    """敌人（主公）是【桃园结义】的目标：主公先受伤，否则该目标在 C1 就被判无效、不开窗。"""
    return _nullify_query(
        trick="peach_garden",
        user=0,
        targets=(0, 1, 2, 3, 4),
        identities=(Identity.LORD, Identity.REBEL, Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE),
        ai_seat=1,
        hp={0: 3},
    )


def _nested_nullify_query():
    """嵌套无懈：座位 2 先声明一张无懈，AI（座位 4，反贼）在无懈自己的窗口里被问。"""
    return _nullify_query(
        trick="ex_nihilo",
        user=0,
        targets=(0,),
        identities=(Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.REBEL),
        ai_seat=4,
        declare_at=2,
        nested=True,
    )


_NULLIFY_FIXTURES = (
    ("own_ex_nihilo", _own_ex_nihilo_query, "ex_nihilo", 0, False),
    ("enemy_ex_nihilo", _enemy_ex_nihilo_query, "ex_nihilo", 0, False),
    ("ally_duel", _ally_duel_query, "duel", 0, False),
    ("enemy_indulgence", _enemy_indulgence_query, "indulgence", 0, False),
    ("enemy_peach_garden", _enemy_peach_garden_query, "peach_garden", 0, False),
    ("nested_nullify", _nested_nullify_query, "nullify", 2, True),
)
"""六条用例的夹具读数：名字 / 构造器 / 处理区顶牌牌名 / ``query.target`` / 是否嵌套。"""


def test_the_nullify_fixtures_reach_real_nullify_windows():
    """夹具自检：停下来的真的是 AI 的无懈询问、处理区里真的是该牌、候选里真有两条路。

    与【遗计】自检同理：这里的断言全部是**形状**断言（不含"宣 / 不宣"的策略判据），
    夹具被引擎改动弄坏时保持可见，而不是把下方六条用例一起拖红。
    """
    for name, build, expected_card, expected_target, nested in _NULLIFY_FIXTURES:
        query = build()
        assert _is_nullify_window(query), f"{name}：不是无懈窗口"
        assert query.required == NULLIFY, f"{name}：窗口要求的不是无懈"
        assert query.view.processing, f"{name}：无懈窗口里处理区是空的"
        top = query.view.processing[-1]
        assert top.type == expected_card, f"{name}：处理区顶牌是 {top.type}"
        assert query.target == expected_target, f"{name}：窗口目标是 {query.target}"
        assert any(
            isinstance(action, UseCard) and action.card.type == NULLIFY
            for action in query.legal_actions
        ), f"{name}：候选里没有【无懈可击】——'宣'不是因为合法性"
        assert any(isinstance(action, Pass) for action in query.legal_actions), (
            f"{name}：候选里没有 Pass——'不宣'不是真的选择"
        )
        for seed in TIE_SEEDS:
            action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
            assert action in query.legal_actions, f"{name}：AI 应答不在真实候选里"


def test_d1_passes_on_its_own_ex_nihilo():
    """自己【无中生有】：取消它只是自断过牌 ⇒ ``Pass``（票面 6 条里的第 1 条）。"""
    _assert_nullify_answer(_own_ex_nihilo_query(), declare=False)


def test_d1_declares_on_an_enemy_ex_nihilo():
    """敌人【无中生有】：取消它 = 少给敌人两张牌 ⇒ 宣（第 2 条）。"""
    _assert_nullify_answer(_enemy_ex_nihilo_query(), declare=True)


def test_d1_declares_on_an_ally_duel():
    """盟友（已亮明主公）被【决斗】：取消伤害 ⇒ 宣（第 3 条）。"""
    _assert_nullify_answer(_ally_duel_query(), declare=True)


def test_d1_passes_on_an_enemy_indulgence():
    """敌人的【乐不思蜀】目标：替他拆掉乐才是亏 ⇒ ``Pass``（第 4 条）。"""
    _assert_nullify_answer(_enemy_indulgence_query(), declare=False)


def test_d1_declares_on_an_enemy_peach_garden():
    """敌人【桃园结义】目标：取消他的一次回复 ⇒ 宣（第 5 条）。"""
    _assert_nullify_answer(_enemy_peach_garden_query(), declare=True)


def test_d1_passes_on_a_nested_nullify():
    """嵌套无懈：不做链求解 ⇒ ``Pass``（第 6 条）。"""
    _assert_nullify_answer(_nested_nullify_query(), declare=False)
