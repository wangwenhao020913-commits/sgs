"""AI 决策级（D 类）确定性用例：**构造局面 + 一次真实询问 + 可接受集合**。

缝与 spec §2.2 / §Testing「A. AI 应答面」逐字一致：

``tests.support.positions.build_state`` → ``Engine.from_state`` → 既有
``run_queries`` 推进到目标询问 → 直接调用**真实** ``HeuristicPlayer.respond(query)``。
**不 mock、不旁路候选**：候选一律取自这次真实询问的 ``legal_actions``；复合动作由真实
引擎校验器裁决（本文件当前的用例没有复合动作）。

判据形状（spec §2.2）：``action ∈ 可接受集合`` **且** ``action ∉ 已知蠢点集合``；
精确相等只在"候选本来唯一"时用。

**在修复前是红的** D 类用例先以 ``xfail(strict=True)`` 落地（spec §2.2「先红后绿」）：
strict 会在修好后变 XPASS、门禁因此判红，逼实现者摘标记，不靠自觉。**修复前已绿的护栏
用例不加标记**——strict 会把 XPASS 判红；哪些是"先红"、哪些是护栏，逐条写在用例 docstring
里（票 18 的 3/6、票 19 的 1/4 都是这个偏差）。被拒的条目保留 xfail 并写明原因。
"""

from __future__ import annotations

import pytest

from sgs.agents import HeuristicPlayer
from sgs.agents import heuristic as heuristic_scoring
from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Engine, ai_stream
from sgs.effects import behavior_for, is_slash
from sgs.skills import hero_card_zone_key
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    MoveReason,
    Pass,
    Phase,
    SelectCards,
    SelectOption,
    SkillActivationQuery,
    UseCard,
    WindowCause,
    Zone,
    project,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    find_action,
    ran_timing,
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
"""平分抽签的种子样本（D3 的"平分"与 D1 的"不许靠平分"共用）。

【遗计】收牌人在票 24 之前是打分**平分**（自己与主公同为 ``_SEAT_TARGET_VALUE``），
由 ``ai:<seat>`` 子流抽签决定。单一 seed 只能证明"某一次抽签抽到了谁"，故在固定
若干种子上要求**同一个**性质成立：票 24 修好后 8 个种子恒给主公；若有人把评分改回
平分，至少一个 seed 会抽到自己，用例必红。它不是统计断言——输入输出全部确定。

D1 也复用它：无懈候选若被记成与 ``Pass`` 同分（0），"不该宣"的判据就会由同一条
子流抽签决定；跑满这 8 个 seed 让"平分"的实现不能靠运气通过。"""


class _YijiDriver:
    """把局面推到【遗计】的询问：主公打出【杀】、只发动【遗计】、选牌取一张。

    调用方用停止条件决定停在哪一步（"选一至两张亮牌"的 ``SELECT_CARDS``，或其后
    "交给谁"的 ``SELECT_OPTION``）；本驱动只负责把伤害与技能发动这两步走完。
    """

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


def _yiji_engine():
    """构造 5 人局并返回引擎：主公坐 0 号、出牌阶段对 1 号 AI 使用【杀】；AI 是忠臣郭嘉。

    伤害由真实的【杀】命中产生，不另压合成 ``DAMAGE`` 帧。
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
    return Engine.from_state(state, seed=7)


def _yiji_pending(stop, *, label: str):
    """推进到 ``stop(engine)`` 为真，返回引擎停在的那条未应答询问（必属 AI）。"""
    engine = _yiji_engine()
    run_queries(
        engine,
        [_YijiDriver() for _ in range(len(engine.state.players))],
        stop,
    )
    query = engine.pending
    assert query is not None, f"推进器在到达【遗计】{label}前就停了"
    assert query.actor == AI_SEAT, f"{label}必须发给受伤的 AI 自己"
    return query


def _yiji_give_query():
    """「交给谁」询问（尚未应答）——票 24 的 D 用例都停在这里。"""
    return _yiji_pending(
        lambda current: current.pending is not None
        and current.pending.kind == "SELECT_OPTION"
        and str(getattr(current.pending, "skill_id", "")).startswith("yiji:give"),
        label="收牌人询问",
    )


def _yiji_pick_query():
    """【遗计】的"选一至两张亮牌"询问（尚未应答）——此时收牌人还没选。"""
    return _yiji_pending(
        lambda current: current.pending is not None
        and current.pending.kind == "SELECT_CARDS"
        and current.pending.view.seat == AI_SEAT,
        label="选牌询问",
    )


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

    这条**只核形状**：夹具将来被引擎改动弄坏时先在这里变红，而不是与下方 D 用例的策略
    判据混成同一个失败；它同时钉住"AI 的应答一定落在真实候选面内"（应答崩溃不会被吞掉）。
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


def test_yiji_gives_to_the_revealed_lord_not_self_or_enemy():
    """D3【遗计】收牌人：5 人局 AI 忠臣、主公已亮明 ⇒ 收牌人是主公，不是自己 / 敌人。

    票 24 修的就是这个蠢点：修复前"给自己"与"给主公"同分（都由帮助类的 ``+2`` 得到），
    收牌人由平分抽签决定；修复后给自己 = no-op（0 分），已亮明的盟友严格更高。
    断言不手抄候选：可接受答案由询问视角里的主公身份算出。
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


def test_the_yiji_give_step_scores_self_as_a_no_op_and_allies_over_enemies():
    """D3 的打分口径（票 24）：给自己 = no-op（0）、盟友正分、敌人负分。

    这条**先红后绿**：修复前给自己是帮助类的 ``+2``（断言红），票 24 把它改成 0.0；
    它不带 xfail——红用例是上面动作级的那条 D，这里是同一票的分数级判据。

    动作级用例只钉"给主公"这一个点；这里把 spec 那句"给自己 = no-op、盟友正、敌人负"
    直接钉在分数上——否则"给主公"可以由任何把主公抬到最高的改动满足，而"交给自己等于
    没交"的语义没有判据。座位 2–4 在视角里身份未知，但 5 人局忠臣视角把已亮明的主公与
    自己从配比里减掉后，剩下的全是敌人 ⇒ 后验 P(敌) = 1、负分（I1 的权重只做幅度）。
    """
    query, lord, _offered = _yiji_case()
    score = heuristic_scoring._score_seat_option

    assert score(query, f"seat:{query.view.seat}") == 0.0, (
        "把牌交给自己 = 没有交出 ⇒ no-op（0 分）"
    )
    assert score(query, f"seat:{lord}") > 0.0, "已亮明的盟友必须正分"
    for enemy in ENEMY_SEATS:
        assert score(query, f"seat:{enemy}") < 0.0, "敌人必须负分"


def test_the_yiji_pick_step_gives_one_card_while_the_recipient_is_unknown():
    """D3 的另一半（票 24）：选牌步时收牌人还没选 ⇒ 按最坏情形只交 1 张。

    收牌人在**下一步**（``SELECT_OPTION`` 的 ``seat:N``）才选，故选牌步只能按最坏情形
    考虑（这些牌可能落到敌人手里）：给出的牌越多越亏，1 张优于两张一起选。现有打分正是
    这个结果——自己的牌按成本估（``_step_card_score`` 的 ``-牌价``），**打分器本身不读
    收牌人**；这条护栏钉住的是结果，不是某个"敌方贴现"实现。修复前已绿（选牌步按"自己的
    牌 = 成本"估值），本票把它钉成护栏——将来若给选牌步加"给盟友两张"的正分，必须同时
    补上"这一批 + 可能的收牌人"的联合视图（spec §6.6 的 v2），不能只改一边。
    """
    query = _yiji_pick_query()
    assert query.kind == "SELECT_CARDS"
    assert query.spec.total == (1, 2), "【遗计】的选牌步仍该提供「一张 / 两张」两条路"
    assert not query.spec.optional, "技能一旦发动就必然要交出，选牌步没有 Pass"

    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, AI_SEAT)).respond(query)
        assert isinstance(action, SelectCards), f"预期选牌动作，实得 {action!r}"
        assert len(action.picks) == 1, (
            f"收牌人未知时按最坏情形只给 1 张，实得 {len(action.picks)} 张"
        )


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
    dead: tuple[int, ...] = (),
    ai_hero: str | None = None,
    ai_spare: int | None = None,
):
    """把局面推到 ``ai_seat`` 的一条真实无懈询问。

    - ``user`` 在出牌阶段使用 ``trick``（目标 ``targets``）；
    - AI 座位手里恒有【无懈可击】，故"宣 / 不宣"是真的选择（不是合法性碰巧不成立）；
    - ``nested=True`` 时另让 ``declare_at`` 先声明一张无懈，于是停下时**处理区顶牌是
      无懈本身**——嵌套窗口靠这个状态与第一层区分；
    - ``dead``（票 21 的 I1 用例）：这些座位开局即阵亡、0 体力——**阵亡即亮明身份**，
      身份后验因此能看见"配比里少了一个谁"。默认空 = 既有六条用例的局面逐字节不变。
    - ``ai_hero`` / ``ai_spare``（票 03）：AI 座位换成指定武将（于吉 = 有无懈窗口的
      **提供者**候选），另发一张手牌给它扣置。默认 ``None`` = 上面六条用例的局面不变。

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
    if ai_spare is not None:
        hands[ai_seat] = hands.get(ai_seat, ()) + (ai_spare,)
        used.add(ai_spare)
    hp_overrides = dict(hp or {})
    seats = [
        SeatSpec(
            hp=0 if seat in dead else hp_overrides.get(seat, 4),
            hp_max=4,
            alive=seat not in dead,
            identity=identity,
            hero=ai_hero if seat == ai_seat else None,
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


def _guohuo_own_ex_nihilo_query():
    """自己【无中生有】、AI 座位是于吉移动版：无懈窗口里多一条【蛊惑】（票 03 的提供者）。

    这是"提供者也是响应牌"的判据面：把真【无懈可击】换成扣置一张手牌当【无懈可击】之后，
    "该不该宣"的结论必须**一字不变**（这里 = 不宣）。
    """
    return _nullify_query(
        trick="ex_nihilo",
        user=0,
        targets=(0,),
        identities=(Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
        ai_seat=0,
        ai_hero="yuji_mobile",
        ai_spare=cards_of_type(PACK, "slash")[1],
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
        assert len(query.view.processing) == (2 if nested else 1), (
            f"{name}：处理区张数不是 {2 if nested else 1}"
        )
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


def test_d1_treats_a_provider_like_a_real_nullify_in_the_nullify_window():
    """【蛊惑】在无懈窗口里也是响应牌：该不宣就不宣（票 03 的 AI 面）。

    先红点：``_score_respond`` 曾给 ``ActivateSkill`` 无条件 :data:`_DODGE_SKILL_VALUE`
    （正分），于是 AI 会用【蛊惑】去宣一张**换成真无懈它自己会 Pass** 的无懈。
    """
    query = _guohuo_own_ex_nihilo_query()

    assert any(isinstance(action, ActivateSkill) for action in query.legal_actions), (
        "候选里必须有【蛊惑】这条提供者路径——否则这条用例测的不是它"
    )
    _assert_nullify_answer(query, declare=False)


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


# ---------------------------------------------------------------------------
# D2 斩杀优先（票 19）：伤害已够就先斩，别把残局放跑
# ---------------------------------------------------------------------------

PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 档：本节的【酒】/【藤甲】只在这一档出现（显式选档，不依赖产品默认）。"""

WINE_160 = cards_of_type(PACK_160, "wine")[0]
SLASH_160 = cards_of_type(PACK_160, "slash")[0]
VINE_ARMOUR_160 = cards_of_type(PACK_160, "vine_armor")[0]


def _inert_draws(pack, *, count: int, used: set[int]) -> tuple[int, ...]:
    """牌堆顶 ``count`` 张「惰性牌」（【闪】）：出牌阶段没有使用候选，摸到它们不改变决策面。

    避开 ``used``（构造在座位上的实体牌）保证一局里不出现重复的实体牌。
    """
    top = tuple(card_id for card_id in cards_of_type(pack, "dodge") if card_id not in used)[
        :count
    ]
    assert len(top) == count, "牌堆顶的惰性【闪】不够（构造局面写错了）"
    return top


def _deck_with_inert_top(pack, *, seats, count: int) -> tuple[int, ...]:
    """牌堆序：顶上 ``count`` 张惰性牌 + 其余不重复的实体牌（构造局面共用）。

    惰性牌吸收摸牌阶段（见 :func:`_inert_draws`）；D2 的 `_lethal_play_session` 与 S1 的
    贯石斧夹具都从这里取，不各抄一份"顶牌 + 剩余"的拼法。
    """
    used = {card_id for spec in seats for card_id in (*spec.hand, *spec.equip)}
    top = _inert_draws(pack, count=count, used=used)
    reserved = used | set(top)
    return (
        *top,
        *(card_id for card_id in range(len(pack.deck.cards)) if card_id not in reserved),
    )


def _lethal_play_session(*, pack=PACK, seats, ai_seat: int, inert: int):
    """推进到 ``ai_seat`` 的**第一个** ``PLAY_PHASE``，返回 ``(engine, query)``。

    **先手座位不是随便选的**（票 19 的构造纪律）：身份局的先手是**主公座位**、无身份局
    读 ``RuleConfig.first_seat``（``engine._first_seat``）。故身份局用例把 AI 放在主公座位
    上（或主公次席——见 :func:`_enemy_vs_unknown_query`），并用牌堆顶的惰性【闪】吸收
    这之前所有摸牌阶段：AI 的决策面因此只剩构造的手牌。

    与 D1 / D3 同一驱动姿态：真局面 → ``Engine.from_state`` → ``run_queries`` → 真
    ``HeuristicPlayer.respond``；本文件不抄候选。**返回 engine** 的用处只在一处：② 要真的
    把【酒】提交进去、再问同一座位的下一条应答（"先酒再杀"是一条两步链）。
    """
    state = build_state(
        pack=pack,
        rule_config=RuleConfig(player_count=len(seats)),
        seats=seats,
        deck=_deck_with_inert_top(pack, seats=seats, count=inert),
        turn_seat=ai_seat,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(
        engine,
        [PassPlayer() for _ in seats],
        lambda current: current.pending is not None
        and current.pending.kind == "PLAY_PHASE"
        and current.pending.actor == ai_seat,
    )
    query = engine.pending
    assert query is not None, "推进器在到达 AI 的出牌阶段前就停了"
    return engine, query


def _lethal_play_query(*, pack=PACK, seats, ai_seat: int, inert: int):
    """只要询问的简写（四条夹具与自检用；② 另走 :func:`_lethal_play_session`）。"""
    return _lethal_play_session(
        pack=pack, seats=seats, ai_seat=ai_seat, inert=inert
    )[1]


def _lethal_vs_full_query():
    """1 体力 0 手 vs 4 体力 0 手：两席都对 AI **未知身份**、都不在连环 / 都没装备。

    AI = 主公（座位 2，身份局先手）；两个目标（座位 1 / 3）与主公相邻、手牌为空。
    两席的关系相同（未知）、手牌数相同（0）⇒ 修复前两者**同分**（平分抽签），
    修复后只按体力差取胜。
    """
    return _lethal_play_query(
        seats=[
            SeatSpec(hp=4, hp_max=4, identity=Identity.LOYALIST),
            SeatSpec(hp=1, hp_max=4, identity=Identity.RENEGADE),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LORD, hand=(SLASH_ID,)),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
        ],
        ai_seat=2,
        inert=2,
    )


def _wine_slash_seats():
    """AI = 座位 0 握【酒】+【杀】；座位 1 = 2 体力空手目标（无身份 2 人局）。"""
    return [
        SeatSpec(hp=4, hp_max=4, hand=(WINE_160, SLASH_160)),
        SeatSpec(hp=2, hp_max=4),
    ]


def _wine_slash_session():
    """② 的两步链要真的提交【酒】（见 :func:`test_d2_drinks_wine_before_a_lethal_slash`）。"""
    return _lethal_play_session(
        pack=PACK_160, seats=_wine_slash_seats(), ai_seat=0, inert=2
    )


def _wine_slash_query():
    """AI 握【酒】+【杀】，目标 2 体力 0 手 ⇒ 这一刀差 1 点正好斩杀（军争 160 档）。

    无身份 2 人局（先手 = 座位 0 = AI）：没有身份关系参与，判据只剩“酒 + 杀能不能斩”。
    """
    return _wine_slash_session()[1]


def _enemy_vs_unknown_query():
    """已明敌人（主公，3 手）vs 未知身份（0 手）：两者 4 体力、都不可一杀。

    AI = 座位 3 的反贼；主公坐 2（第一回合，出牌阶段直接结束）⇒ 到 AI 时主公手牌
    1 + 2 = 3 张。座位 4 的未知身份座位空手。两者都在【杀】的距离内。
    """
    return _lethal_play_query(
        seats=[
            SeatSpec(hp=4, hp_max=4, identity=Identity.LOYALIST),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(
                hp=4,
                hp_max=4,
                identity=Identity.LORD,
                hand=(cards_of_type(PACK, "dodge")[3],),
            ),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL, hand=(SLASH_ID,)),
            SeatSpec(hp=4, hp_max=4, identity=Identity.RENEGADE),
        ],
        ai_seat=3,
        inert=4,
    )


def _vine_target_query():
    """1 体力目标穿【藤甲】，AI 只有普通【杀】：这一杀**不造成伤害**（不是斩杀）。

    无身份 2 人局（先手 = 座位 0 = AI），场上只有这一个目标 ⇒ “打不打”不被别的目标干扰。
    """
    return _lethal_play_query(
        pack=PACK_160,
        seats=[
            SeatSpec(hp=4, hp_max=4, hand=(SLASH_160,)),
            SeatSpec(hp=1, hp_max=4, equip=(VINE_ARMOUR_160,)),
        ],
        ai_seat=0,
        inert=2,
    )


def _lethal_target_with_cards_query():
    """1 体力 **2 手** vs 4 体力 0 手（两席对 AI 都未知）：斩杀项**不看手牌张数**。

    票面那句“伤害已够且目标 0 手牌（不可能有【闪】）”与 spec §6.1 D2 的“改什么”
    （只要求 ``_damage_to >= 体力``）有口径差：本票按 **spec 的“改什么”** 落地——只看
    实际伤害够不够，手牌张数只进既有的防守贴现（这里 1 体力目标 2 手 ⇒ −0.5），概率版
    P(闪) 归 C1（票 20）。这条用例把该读法钉住，并留给人类裁定（见 `## Answer`）。
    """
    dodges = cards_of_type(PACK, "dodge")
    return _lethal_play_query(
        seats=[
            SeatSpec(hp=4, hp_max=4, identity=Identity.LOYALIST),
            SeatSpec(hp=1, hp_max=4, identity=Identity.RENEGADE, hand=tuple(dodges[4:6])),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LORD, hand=(SLASH_ID,)),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
        ],
        ai_seat=2,
        inert=2,
    )


def _slash_targets(query) -> set[int]:
    """真实候选里**【杀】族**能落在哪些座位（单目标候选；不手抄候选）。

    族判据走行为表（``is_slash(behavior_for(card))``）——与产品打分器同一份判据，不在这里
    按牌名另抄一张集合（否则第五条【杀】又得补一次）。
    """
    return {
        action.targets[0]
        for action in query.legal_actions
        if isinstance(action, UseCard)
        and action.targets
        and is_slash(behavior_for(action.card))
    }


def test_the_lethal_fixtures_reach_real_play_phase_windows():
    """夹具自检（不带 xfail）：停下来的真的是 AI 的出牌阶段，且四条判据的“两条路”都在。

    与 D1 / D3 的自检同理：这里只核**形状**（询问归属、目标体力 / 手牌、候选面），不核
    “该打谁”的策略判据——夹具被引擎改动弄坏时保持可见，而不是把下方用例一起拖红。
    """
    lethal = _lethal_vs_full_query()
    assert lethal.kind == "PLAY_PHASE" and lethal.actor == 2
    assert {seat: lethal.view.players[seat].hp for seat in _slash_targets(lethal)} == {
        1: 1,
        3: 4,
    }, "夹具失效：两个目标不再是 1 体力 / 4 体力"
    assert all(
        lethal.view.players[seat].hand_count == 0 for seat in _slash_targets(lethal)
    ), "夹具失效：目标不再空手（会有【闪】的可能）"

    wine = _wine_slash_query()
    assert wine.kind == "PLAY_PHASE" and wine.actor == 0
    assert any(
        isinstance(action, UseCard) and action.card.type == "wine"
        for action in wine.legal_actions
    ), "夹具失效：出牌阶段没有【酒】的候选"
    (wine_target,) = _slash_targets(wine)
    assert wine.view.players[wine_target].hp == 2, "夹具失效：目标不再正好差【酒】的 1 点"

    enemy = _enemy_vs_unknown_query()
    assert enemy.kind == "PLAY_PHASE" and enemy.actor == 3
    targets = _slash_targets(enemy)
    lord = next(
        seat
        for seat, player in enumerate(enemy.view.players)
        if seat != enemy.actor and player.identity is Identity.LORD
    )
    assert lord in targets, "夹具失效：已明敌人不在【杀】的候选里"
    assert {seat: enemy.view.players[seat].hand_count for seat in targets} == {
        lord: 3,
        4: 0,
    }, "夹具失效：目标不再是“敌人 3 手 / 未知 0 手”"

    vine = _vine_target_query()
    assert vine.kind == "PLAY_PHASE" and vine.actor == 0
    (vine_target,) = _slash_targets(vine)
    assert vine.view.players[vine_target].hp == 1, "夹具失效：藤甲目标不再在 1 体力"
    assert any(
        card.type == "vine_armor" for card in vine.view.players[vine_target].equip
    ), "夹具失效：目标不再穿【藤甲】"

    cards = _lethal_target_with_cards_query()
    assert cards.kind == "PLAY_PHASE" and cards.actor == 2
    assert {seat: (cards.view.players[seat].hp, cards.view.players[seat].hand_count) for seat in _slash_targets(cards)} == {
        1: (1, 2),
        3: (4, 0),
    }, "夹具失效：不再是“1 体力 2 手 vs 4 体力 0 手”"

    for query in (lethal, wine, enemy, vine, cards):
        for seed in TIE_SEEDS:
            action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
            assert action in query.legal_actions, "AI 应答必须落在真实候选面上"


def test_d2_strikes_the_lethal_target_over_the_healthy_one():
    """D2 斩杀优先：1 体力 0 手 vs 4 体力 0 手 ⇒ 打 1 体力（残局不放跑）。

    修复前两席同分（关系未知、都空手）⇒ 打谁由 ``ai:<seat>`` 平分抽签决定，跑满
    #TIE_SEEDS 必有种子打 4 体力那一席（`6abb5c3` 上它带 ``xfail(strict=True)``）；
    修复后按“伤害 ≥ 体力”的斩杀项取胜，与抽签无关。

    **精确相等在这里成立**：这把【杀】只有两条候选（两个目标），而“打 1 体力”这个判据
    的答案就是一个单元素集合——不是把“候选本来唯一”当借口（spec §2.2）。
    """
    query = _lethal_vs_full_query()
    lethal_seat = next(
        seat for seat in _slash_targets(query) if query.view.players[seat].hp == 1
    )
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert isinstance(action, UseCard), f"预期出【杀】，实得 {action!r}"
        assert action.targets == (lethal_seat,), (
            f"应当先斩 1 体力目标（座位 {lethal_seat}），实得 {action.targets}"
        )


def test_d2_drinks_wine_before_a_lethal_slash():
    """D2 斩杀优先：【酒】+【杀】正好斩杀 ⇒ 先酒，**再**补上那张【杀】（两步都真的提交）。

    **修复前已绿**（M5-07 的【酒】取舍），故不加 xfail（strict 会把 XPASS 判红）。它是
    护栏：本票把“斩杀”收成一个共享判据后，这条仍是“先酒再杀”，且杀完那一步真的落到
    同一个目标上（只断言“先酒”会漏掉第二步的回归）。
    """
    for seed in TIE_SEEDS:
        engine, query = _wine_slash_session()
        (target,) = _slash_targets(query)
        player = HeuristicPlayer(ai_stream(seed, query.actor))

        first = player.respond(query)

        assert isinstance(first, UseCard), f"预期使用【酒】，实得 {first!r}"
        assert first.card.type == "wine", f"预期先【酒】再【杀】，实得 {first.card.type}"
        assert first.targets == (query.actor,), "出牌阶段的【酒】只对自己"

        engine.submit(first)
        run_queries(
            engine,
            [player, *(PassPlayer() for _ in range(len(query.view.players) - 1))],
            lambda current: current.pending is not None
            and current.pending.kind == "PLAY_PHASE"
            and current.pending.actor == query.actor,
        )

        second = player.respond(engine.pending)

        assert isinstance(second, UseCard), f"【酒】之后该补【杀】，实得 {second!r}"
        assert second.targets == (target,), (
            f"【杀】该落在同一目标（座位 {target}），实得 {second.targets}"
        )


def test_d2_prefers_a_revealed_enemy_when_no_target_is_lethal():
    """D2 斩杀优先：已明敌人 3 手 vs 未知 0 手（都 4 体力、都不可一杀）⇒ 打敌人。

    **修复前已绿**（``_ENEMY_FOCUS`` 大于手牌防御贴现），故不加 xfail。它是护栏：新增的
    斩杀项不许把“手牌少的那一席”捧成首选，也不许盖过已明敌人。
    """
    query = _enemy_vs_unknown_query()
    lord = next(
        seat
        for seat, player in enumerate(query.view.players)
        if seat != query.actor and player.identity is Identity.LORD
    )
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert isinstance(action, UseCard), f"预期出【杀】，实得 {action!r}"
        assert action.targets == (lord,), (
            f"不可一杀时应打已明敌人（座位 {lord}），实得 {action.targets}"
        )


def test_d2_a_normal_slash_does_not_become_lethal_through_vine_armour():
    """D2 斩杀优先：普通【杀】打【藤甲】不造成伤害 ⇒ 斩杀项不许把它变有效。

    **修复前已绿**（``_WASTED_ATTACK_PENALTY``），故不加 xfail。它是护栏：斩杀项必须读
    ``_damage_to`` 的实际伤害——藤甲把普通【杀】挡成 0，【酒】/ 古锭刀的加伤照常算，
    白银狮子把伤害封在 1——不许拿“体力 ≤ 裸伤害”把一张无效的【杀】捧起来。
    """
    query = _vine_target_query()
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert isinstance(action, EndPhase), (
            f"普通【杀】打藤甲无效 ⇒ 宁可结束阶段，实得 {action!r}"
        )


def test_d2_strikes_a_lethal_target_that_still_holds_cards():
    """D2 斩杀优先：1 体力 **2 手** vs 4 体力 0 手 ⇒ 仍打能斩的那一席（不看手牌张数）。

    票面那句“伤害已够且目标 0 手牌（不可能有【闪】）”与 spec §6.1 D2 的“改什么”
    （只要求 ``_damage_to >= 体力``）有口径差，本票按 **spec 的“改什么”** 落地；这条用例
    把该读法钉住（修复前 3.5 < 4.0 会打 4 体力那席，修复后 3.5 + 斩杀项 > 4.0）。概率版
    P(闪) 归 C1（票 20）——评审时若裁定要“0 手牌门”，改法是在 ``_score_attack`` 的斩杀项
    上加 ``hand_count == 0`` 并把这条用例的期望反过来。
    """
    query = _lethal_target_with_cards_query()
    lethal_seat = next(
        seat
        for seat in _slash_targets(query)
        if query.view.players[seat].hp == 1 and query.view.players[seat].hand_count > 0
    )
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert isinstance(action, UseCard), f"预期出【杀】，实得 {action!r}"
        assert action.targets == (lethal_seat,), (
            f"应当仍斩能杀的那一席（座位 {lethal_seat}），实得 {action.targets}"
        )


# ---------------------------------------------------------------------------
# C1 记牌 P(闪)（票 20）：闪全可见 ⇒ 不贴现；档位 / 守恒对不上 ⇒ 关闭记牌
# ---------------------------------------------------------------------------

COUNTING_DODGES = cards_of_type(PACK, "dodge")
"""standard_108 的全部 15 张【闪】（``card_id`` 即数据表下标，不是牌面对象）。"""

COUNTING_TARGET_HAND = tuple(cards_of_type(PACK, "peach")[:4])
"""4 手目标的手牌：全是【桃】（非【闪】）——只占"手牌数"，不改变未知池里的闪剩几张。"""

COUNTING_SPARE = cards_of_type(PACK, "slash")[1]
"""守恒破坏用：这张【杀】既不入手也不进弃牌堆 / 牌堆顶，整局因此少一张实体牌。"""

COUNTING_AI_SEAT = 2
"""AI = 主公（身份局先手）；1 号 4 手、3 号 0 手，两者 4 体力、身份对 AI 都未知。"""


def _counting_play_query(*, drop: tuple[int, ...] = ()):
    """推进到 AI 的 ``PLAY_PHASE``；``drop`` 把实体牌从整局里拿掉（破坏守恒量）。

    - 弃牌堆放 13 张【闪】、牌堆顶 2 张【闪】：AI 摸牌后手里 2 张【闪】⇒ **15 张全可见**，
      未知池（牌堆 88 张）里一张【闪】也没有 ⇒ P(闪) = 0；
    - 座位 1 的 4 张手牌全用【桃】⇒"4 手"只影响常量贴现，不改变未知池；
    - ``drop`` 默认空：108 张全在局内（守恒量对得上 ``standard_108``）。
    """
    ai_card = cards_of_type(PACK, "slash")[0]
    inert = tuple(COUNTING_DODGES[13:])
    used = {ai_card, *COUNTING_TARGET_HAND, *COUNTING_DODGES[:13], *inert}
    deck = (
        *inert,
        *(
            card_id
            for card_id in range(len(PACK.deck.cards))
            if card_id not in used and card_id not in drop
        ),
    )
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=5),
        seats=[
            SeatSpec(hp=4, hp_max=4, identity=Identity.LOYALIST),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL, hand=COUNTING_TARGET_HAND),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LORD, hand=(ai_card,)),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hp_max=4, identity=Identity.RENEGADE),
        ],
        discard=COUNTING_DODGES[:13],
        deck=deck,
        turn_seat=COUNTING_AI_SEAT,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(
        engine,
        [PassPlayer() for _ in state.players],
        lambda current: current.pending is not None
        and current.pending.kind == "PLAY_PHASE"
        and current.pending.actor == COUNTING_AI_SEAT,
    )
    query = engine.pending
    assert query is not None, "推进器在到达 AI 的出牌阶段前就停了"
    return query


def _view_card_faces(view) -> list:
    """``PlayerView`` 里**牌面可见**的那些牌（自己手牌 + 四公开区）。

    与产品的 ``_visible_card_faces`` 分开写：夹具自检要独立核"15 张闪真的都在可见区"，
    调产品实现就成了同义反复。名字也避开产品的 ``_visible_cards``（那张表不含共享区、语义不同）。
    """
    cards = [*view.self_hand, *view.discard, *view.processing]
    for player in view.players:
        cards.extend(player.equip)
        cards.extend(player.judge)
    return cards


def _chosen_targets(query) -> set[int]:
    """跑满平分子流种子，收集 AI 真的出【杀】打到的座位。"""
    chosen: set[int] = set()
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert isinstance(action, UseCard), f"预期出【杀】，实得 {action!r}"
        chosen.add(action.targets[0])
    return chosen


def _empty_hand_target(query) -> int:
    """本构造里 0 手的目标（座位 3）；从真实询问算，不写死。"""
    (seat,) = (
        target
        for target in _slash_targets(query)
        if query.view.players[target].hand_count == 0
    )
    return seat


def test_the_counting_fixtures_reach_real_play_phase_windows():
    """夹具自检（不带 xfail）：询问归属、15 张闪全可见、"4 手 vs 0 手"两条路都在。

    与上面两节的自检同理：只核形状，不核"该打谁"的策略判据。两条 D 用例整体是 strict
    xfail，夹具被引擎改动弄坏时要保持可见，而不是被 xfail 一起吞掉。
    """
    query = _counting_play_query()
    assert query.kind == "PLAY_PHASE" and query.actor == COUNTING_AI_SEAT
    visible = _view_card_faces(query.view)
    assert (
        sum(1 for card in visible if card.type == "dodge") == len(COUNTING_DODGES)
    ), "夹具失效：15 张【闪】不再全在可见区"
    assert {
        seat: (query.view.players[seat].hp, query.view.players[seat].hand_count)
        for seat in _slash_targets(query)
    } == {1: (4, 4), 3: (4, 0)}, "夹具失效：不再是“4 手 vs 0 手、都 4 体力”"

    broken = _counting_play_query(drop=(COUNTING_SPARE,))
    assert broken.kind == "PLAY_PHASE" and broken.actor == COUNTING_AI_SEAT
    assert _slash_targets(broken) == {1, 3}, "夹具失效：少一张牌不该改变【杀】的目标面"
    assert _empty_hand_target(broken) == 3, "夹具失效：0 手目标不再是座位 3"

    for candidate in (query, broken):
        for seed in TIE_SEEDS:
            action = HeuristicPlayer(ai_stream(seed, candidate.actor)).respond(candidate)
            assert action in candidate.legal_actions, "AI 应答必须落在真实候选面上"


def test_c1_when_every_dodge_is_visible_the_hand_count_no_longer_discounts_the_slash():
    """C1 记牌 P(闪)：全部【闪】已在可见区 ⇒ P(闪) = 0、防御贴现 = 0。

    票面允许"动作级优先，写不出来就断言辅助纯函数的公开契约"，这里两条都钉：

    - **纯函数契约**：``_dodge_probability(view, seat)`` 对两个真实目标都答 ``0.0``
      （记牌开着、未知池里没有【闪】）——它只吃 view，不碰任何全知状态；
    - **动作级**：1 号（4 手）与 3 号（0 手）其余条件全同 ⇒ 常量贴现（0.25/张，4 张封顶）
      让 0 手目标恒高 1.0 分；记牌落地后两者同分，8 个平分子流种子必须两边都出现过。
      **修复前这条必红**（``chosen == {3}``）。
    """
    query = _counting_play_query()
    for target in _slash_targets(query):
        assert heuristic_scoring._dodge_probability(query.view, target) == 0.0

    assert _chosen_targets(query) == {1, 3}, (
        "P(闪)=0 时手牌张数不再贴现 ⇒ 两个目标同分、平分子流两边都该出现"
    )


def test_c1_card_counting_switches_off_when_the_tier_or_the_conserved_count_is_unverifiable(
    monkeypatch,
):
    """C1 记牌：档位表被改动 / 守恒量对不上 ⇒ **记牌关闭**、行为与今天一致。

    两条关闭路径都按 spec §6.3 的"不猜"读法钉住：

    - **守恒量对不上**：整局少一张实体牌（107 张）⇒ 三档总张数都对不上 ⇒ 关闭；
    - **档位表被改动**：把 ``standard_108`` 的【闪】数改掉而总数不变（表自身不自洽）⇒ 关闭。
    关闭时 ``_dodge_probability`` 答 ``None``，8 个种子**恒打 0 手那一席**——常量贴现下
    0 手目标高 1.0 分，与今天的行为一致。
    """
    broken = _counting_play_query(drop=(COUNTING_SPARE,))
    empty = _empty_hand_target(broken)
    for target in _slash_targets(broken):
        assert heuristic_scoring._dodge_probability(broken.view, target) is None
    assert _chosen_targets(broken) == {empty}, "守恒量对不上时行为必须与今天一致"

    table = dict(heuristic_scoring._DECK_TABLE)
    total, by_type = table["standard_108"]
    changed = dict(by_type)
    changed["dodge"] -= 1
    table["standard_108"] = (total, changed)
    monkeypatch.setattr(heuristic_scoring, "_DECK_TABLE", table)

    query = _counting_play_query()  # 108 张守恒，但档位表已不自洽
    empty = _empty_hand_target(query)
    for target in _slash_targets(query):
        assert heuristic_scoring._dodge_probability(query.view, target) is None
    assert _chosen_targets(query) == {empty}, "档位表被改动时行为必须与今天一致"


# ---------------------------------------------------------------------------
# C1b 武将牌上的牌（票 27）：【谦逊】放置期间守恒量仍完整、记牌可用
#
# 真流程而不是手搓区：界陆逊的判定区里放一张【乐不思蜀】，判定阶段它"对你生效" ⇒
# 【谦逊】把所有手牌置于自己的武将牌上（区留在那里直到当前回合结束）；随后摸牌阶段
# 摸到两张【杀】，到达 AI 自己的出牌阶段——牌还在武将牌上，正是本票要修的窗口。
# ---------------------------------------------------------------------------

QIANXUN_HAND = tuple(cards_of_type(PACK, "peach")[:2])
"""界陆逊（AI，座位 0）判定阶段发动【谦逊】时手里的 2 张牌（会被搬上武将牌）。"""

QIANXUN_TARGET_HAND = tuple(cards_of_type(PACK, "peach")[2:6])
"""目标（座位 1）的 4 张手牌：全是【桃】，只占手牌数、不改变未知池里的【闪】。"""

QIANXUN_SLASH = tuple(cards_of_type(PACK, "slash")[:2])
"""摸牌阶段摸到的两张【杀】（出牌阶段的可选动作）。"""

QIANXUN_INDULGENCE = cards_of_type(PACK, "indulgence")[0]
"""判定区里的【乐不思蜀】：延时锦囊在判定阶段对界陆逊生效 ⇒ 触发【谦逊】。"""

JIE_LUXUN = "jie_luxun"
JIE_QIANXUN = "hero.jie_luxun.qianxun"
QIANXUN_ZONE = hero_card_zone_key(0, JIE_QIANXUN)


def _qianxun_heart_top(used: set[int]) -> int:
    """牌堆顶那张**红桃**判定牌：【乐不思蜀】判红桃才不跳过出牌阶段（夹具的前提）。"""
    return next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit.value == "heart" and card.type != "slash" and index not in used
    )


class _QianxunDriver:
    """座位 0 的脚本：接受【谦逊】，其余取最小合法应答（【连营】因此放弃）。"""

    def __init__(self) -> None:
        self._fallback = PassPlayer()

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == JIE_QIANXUN:
            activate = find_action(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == JIE_QIANXUN,
            )
            if activate is not None:
                return activate
        return self._fallback.respond(query)


def _qianxun_play_query():
    """推进到 AI（界陆逊，座位 0）的出牌阶段；武将牌上仍压着 2 张【谦逊】放的牌。"""
    used = {
        *QIANXUN_HAND,
        *QIANXUN_TARGET_HAND,
        QIANXUN_INDULGENCE,
        *QIANXUN_SLASH,
    }
    heart_top = _qianxun_heart_top(used)
    used.add(heart_top)
    deck = (
        heart_top,
        *QIANXUN_SLASH,
        *(index for index in range(len(PACK.deck.cards)) if index not in used),
    )
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=2),
        seats=[
            SeatSpec(
                hero=JIE_LUXUN,
                hp=3,
                hp_max=3,
                hand=QIANXUN_HAND,
                judge=(QIANXUN_INDULGENCE,),
            ),
            SeatSpec(hand=QIANXUN_TARGET_HAND),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(
        engine,
        [_QianxunDriver(), PassPlayer()],
        lambda current: current.pending is not None
        and current.pending.kind == "PLAY_PHASE"
        and current.pending.actor == 0,
    )
    query = engine.pending
    assert query is not None, "推进器在到达 AI 的出牌阶段前就停了"
    return engine, query


def test_the_qianxun_fixture_reaches_a_real_play_phase_window():
    """夹具自检（不带 xfail）：询问归属、【谦逊】的牌真的在武将牌上、两个【杀】候选都在。

    只核形状，不核"记牌是否可用"（那两条 D 用例是行为判据；夹具被引擎改动弄坏时要单独
    可见，而不是被行为用例的失败一起掩盖）。
    """
    engine, query = _qianxun_play_query()

    assert query.kind == "PLAY_PHASE" and query.actor == 0
    assert engine.state.hero_zone(0, QIANXUN_ZONE) == QIANXUN_HAND, (
        "夹具失效：【谦逊】没有把手牌留在武将牌上"
    )
    assert [card.card_id for card in query.view.self_hand] == list(QIANXUN_SLASH)
    assert query.view.players[1].hand_count == len(QIANXUN_TARGET_HAND)
    slash_targets = {
        target
        for action in query.legal_actions
        if isinstance(action, UseCard) and action.card.card_id in QIANXUN_SLASH
        for target in action.targets
    }
    assert slash_targets == {1}, "夹具失效：两张【杀】不再指向座位 1"


def test_the_qianxun_placement_is_public_but_faces_stay_host_only():
    """票 27 的隐私判据走**真实产出路径**（`CODING_STANDARDS.md` §6）：真【谦逊】放置后，
    另一个座位的视角只拿张数、不拿牌面；宿主视角牌面齐全。

    与 `test_queries_views_actions.py` 的合成多区用例分工：那条钉"同一座位多区 / 未登记 /
    畸形区"的通用形状（这些区只可能来自测试构造），这条钉**产品调用点**（引擎真的搬了牌）。
    """
    engine, query = _qianxun_play_query()

    foreign = project(engine.state, 1)
    assert foreign.players[0].hero_zone_count == len(QIANXUN_HAND)
    assert foreign.hero_zone_cards == (), "非宿主不拿牌面"

    host = project(engine.state, 0)
    assert host.players[0].hero_zone_count == len(QIANXUN_HAND)
    assert {card.card_id for card in host.hero_zone_cards} == set(QIANXUN_HAND)
    assert query.view.players[0].hero_zone_count == len(QIANXUN_HAND), "询问视角也带上张数"


def test_c1_qianxun_placement_keeps_card_counting_available():
    """票 27 验收①：放置期间守恒量完整、档位能认出来、``_dodge_probability`` 不再答 None。

    修复前 2 张牌移出了六个可数区域又不在 ``PlayerView`` 里 ⇒ 守恒量 106 ⇒ 三档全对不上
    ⇒ 记牌关闭（票 20 的偏差 1）。
    """
    _engine, query = _qianxun_play_query()
    view = query.view

    assert heuristic_scoring._conserved_card_count(view) == 108
    profile = heuristic_scoring._verified_deck_profile(view)
    assert profile is not None, "档位必须能按守恒量认出来"
    assert profile[0] == 108
    assert heuristic_scoring._dodge_probability(view, 1) is not None, "记牌必须恢复可用"


def test_c1_qianxun_placement_keeps_the_slash_priced_by_dodge_probability():
    """票 27 验收②（决策级）：这张【杀】的分数等于记牌路径的期望，而不是常量贴现。

    谓词按票面落成"记牌可用 + 该动作分数等于 ``_expected_attack_score`` 的结果"：
    ``_score_attack`` = 基础分 + 目标分 + 期望增量（2 人无身份局关系项为 0；目标 4 体力
    不构成斩杀）。修复前 ``_dodge_probability`` 答 None ⇒ 分数是常量贴现的 3.0。
    """
    _engine, query = _qianxun_play_query()
    action = find_action(
        query,
        lambda candidate: isinstance(candidate, UseCard)
        and candidate.card.card_id in QIANXUN_SLASH
        and candidate.targets == (1,),
    )
    assert action is not None, "夹具失效：没有指向座位 1 的【杀】候选"

    probability = heuristic_scoring._dodge_probability(query.view, 1)
    assert probability is not None, "记牌必须可用（否则这条退化成常量贴现）"
    expected = (
        heuristic_scoring._DAMAGE_BASE
        + heuristic_scoring._TARGET_BONUS
        + heuristic_scoring._expected_attack_score(1, probability)
    )
    score = heuristic_scoring._score_attack(action, query.view)
    assert score == pytest.approx(expected), (
        "这张【杀】必须按 P(闪) 计价；修复前是常量贴现的 3.0"
    )
    assert score != pytest.approx(3.0), "不能仍是“0.25 × 4 张手牌”的常量贴现"

    # 决策级：真实应答仍走这张【杀】（记牌恢复不改变"该打谁"，只改变分值来源）。
    for seed in TIE_SEEDS:
        answer = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert isinstance(answer, UseCard), f"预期出【杀】，实得 {answer!r}"
        assert answer.targets == (1,)


# ---------------------------------------------------------------------------
# I1 身份保守档（票 21）：已亮明身份 + 配比后验 ⇒ P(敌) / P(友) 替换 0/1
#
# 两条 D 用例在实现前是**红的**（先红后绿，spec §2.2）：今天未知身份一律判"中立"，
# 故"全是敌人"与"内奸先清场"两件事都看不见。用例形状与 D1/D2/C1 相同：真局面 →
# ``Engine.from_state`` → ``run_queries`` → 真 ``HeuristicPlayer.respond``；比例断言落在
# **辅助纯函数** ``_identity_relation(view, seat) -> (P(敌), P(友))`` 上（spec 缝 A 允许的
# 那一条），不碰私有评分器的中间数值。
# ---------------------------------------------------------------------------

I1_REBEL_HAND = tuple(cards_of_type(PACK, "peach")[:2])
"""D2 里未知身份那一席的手牌：只占"2 张手牌"，与主公摸牌后的手牌数 **相等**。

两个目标的体力 / 手牌 / 装备全都相同，修复前它们的分数因此**严格同分**（平分抽签）；
修复后只差身份权重那一项。若这一席空手，手牌贴现已经让未知席位胜出，用例在修复前就
是绿的（strict xfail 会 XPASS 判红），测不到身份后验。
"""

I1_AI_SEAT = 3
"""D2 的内奸座位：5 人身份局的先手是主公（座位 2），故到座位 3 时主公已摸过牌。"""


def _dead_loyalist_ex_nihilo_query():
    """5 人局死 1 忠臣：AI = 主公（座位 0），座位 2 用【无中生有】打自己。

    座位 1 的忠臣**开局即阵亡**（阵亡即亮明身份）：配比减掉主公与忠臣后只剩
    反贼 / 反贼 / 内奸 ⇒ 座位 2 / 3 / 4 三席的 P(敌) 全是 1。座位 2 的【无中生有】对他
    **有利**：取消它 = 少给一个已确定是敌人的座位两张牌，故该宣——今天关系判"未知"
    （既非敌也非友）⇒ 恒不宣。
    """
    return _nullify_query(
        trick="ex_nihilo",
        user=2,
        targets=(2,),
        identities=(
            Identity.LORD,
            Identity.LOYALIST,
            Identity.REBEL,
            Identity.REBEL,
            Identity.RENEGADE,
        ),
        ai_seat=0,
        dead=(1,),
    )


def _renegade_lord_vs_unknown_query():
    """AI = 内奸（座位 3）；已亮明主公（座位 2）与未知身份座位 4，两者 4 体力 / 2 手。

    座位 2 是主公（先手）⇒ 摸牌阶段拿到 2 张；座位 4 构造 2 张 ⇒ 两个目标的分数在修复前
    **严格同分**。两者都在【杀】的距离内（5 人局相邻）。修复后座位 4 的 P(敌) = 1
    （配比减掉主公与内奸后只剩忠臣 / 反贼 / 反贼，全是内奸的对手），主公仍是 0。
    """
    return _lethal_play_query(
        seats=[
            SeatSpec(hp=4, hp_max=4, identity=Identity.LOYALIST),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LORD),
            SeatSpec(
                hp=4,
                hp_max=4,
                identity=Identity.RENEGADE,
                hand=(SLASH_ID,),
            ),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL, hand=I1_REBEL_HAND),
        ],
        ai_seat=I1_AI_SEAT,
        inert=4,
    )


def _relation(view, seat: int) -> tuple[float, float]:
    """辅助纯函数的契约入口（名字收在这里一处，用例只读 ``(P(敌), P(友))``）。"""
    return heuristic_scoring._identity_relation(view, seat)


def test_the_identity_fixtures_reach_real_windows():
    """夹具自检（不带 xfail）：两条 I1 用例的构造面（询问归属 / 身份可见面 / 平分的两条路）。

    与 D1 / D2 / C1 的自检同一姿态：只核形状、不核策略判据。两条 D 用例整体是 strict
    xfail，夹具被引擎改动弄坏时要保持可见，而不是被 xfail 一起吞掉。
    """
    d1 = _dead_loyalist_ex_nihilo_query()
    assert _is_nullify_window(d1) and d1.actor == 0, "D1 没停在主公的无懈询问上"
    assert d1.target == 2, "D1 的窗口目标不是座位 2"
    assert [player.alive for player in d1.view.players] == [
        True,
        False,
        True,
        True,
        True,
    ], "D1 夹具失效：座位 1 不再是唯一阵亡者"
    assert d1.view.players[1].identity is Identity.LOYALIST, (
        "D1 夹具失效：阵亡的忠臣身份没亮明（阵亡即亮明）"
    )
    assert d1.view.processing[-1].type == "ex_nihilo", "D1 夹具失效：处理区顶牌不是无中生有"

    d2 = _renegade_lord_vs_unknown_query()
    assert d2.kind == "PLAY_PHASE" and d2.actor == I1_AI_SEAT, "D2 没停在内奸的出牌阶段"
    targets = _slash_targets(d2)
    lord = next(
        seat
        for seat, player in enumerate(d2.view.players)
        if seat != d2.actor and player.identity is Identity.LORD
    )
    assert targets == {lord, 4}, "D2 夹具失效：可见目标不再是主公与座位 4"
    assert {
        seat: (d2.view.players[seat].hp, d2.view.players[seat].hand_count)
        for seat in targets
    } == {lord: (4, 2), 4: (4, 2)}, "D2 夹具失效：两个目标不再同体力 / 同手牌数"

    for candidate in (d1, d2):
        for seed in TIE_SEEDS:
            action = HeuristicPlayer(ai_stream(seed, candidate.actor)).respond(candidate)
            assert action in candidate.legal_actions, "AI 应答必须落在真实候选面上"


def test_i1_a_dead_loyalist_leaves_the_lord_facing_an_all_enemy_field():
    """I1 身份保守档：5 人局死 1 忠臣 ⇒ 主公视角的**存活未知座位** P(敌) = 1。

    票面允许"动作级或辅助纯函数契约"，两层都钉：

    - **纯函数契约**：``_identity_relation(view, seat)`` 对三个存活未知座位都答
      ``(1.0, 0.0)``——已亮明的身份（本人 / 主公 / 阵亡者）先从配比里减掉，剩下的
      （反贼 / 反贼 / 内奸）按**人头均分**，故 P(敌) 恰好是 1；
    - **动作级**：座位 2 的【无中生有】对他是**有利**的牌 ⇒ 取消它 = 少给一个已确定是敌人
      的座位两张牌 ⇒ 该宣（今天判"未知"⇒ 8 个平分子流种子恒 ``Pass``）。

    修复前这条必红（``_identity_relation`` 还不存在 / 动作级恒 Pass）。
    """
    query = _dead_loyalist_ex_nihilo_query()
    view = query.view
    unknown = [
        seat
        for seat, player in enumerate(view.players)
        if seat != view.seat and player.alive and player.identity is None
    ]
    assert unknown == [2, 3, 4], "夹具失效：存活未知座位不再是 2 / 3 / 4"

    for seat in unknown:
        assert _relation(view, seat) == (1.0, 0.0), (
            f"座位 {seat} 的 P(敌) 不是 1：死 1 忠臣后配比里只剩敌人"
        )

    _assert_nullify_answer(query, declare=True)


def test_i1_the_renegade_keeps_the_lord_off_the_priority_list():
    """I1：内奸语义保留——**不把主公当优先敌人**（先清场、最后弑主）。

    已亮明主公（座位 2）与未知身份（座位 4）同体力 / 同手牌数、都在【杀】的距离内：
    修复前两者都是 0 加权 ⇒ 8 个平分子流种子两边都出现；修复后未知那一席的 P(敌) = 1
    而主公是 0（内奸的敌人是忠臣 / 反贼，主公要留到最后）⇒ **恒打未知那一席**。

    日后的回归若把主公也塞进"内奸的敌人"：这条第一段（``(0.0, 0.0)``）先红。
    """
    query = _renegade_lord_vs_unknown_query()
    view = query.view
    lord = next(
        seat
        for seat, player in enumerate(view.players)
        if seat != view.seat and player.identity is Identity.LORD
    )
    assert _relation(view, lord) == (0.0, 0.0), "内奸不得把主公算成敌人 / 盟友"
    (other,) = _slash_targets(query) - {lord}
    assert _relation(view, other) == (1.0, 0.0), (
        f"座位 {other} 的 P(敌) 不是 1：主公与内奸之外全是内奸的对手"
    )

    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert isinstance(action, UseCard), f"预期出【杀】，实得 {action!r}"
        assert action.targets == (other,), (
            f"内奸该先清场（座位 {other}），不该把主公当优先敌人，实得 {action.targets}"
        )


# ---------------------------------------------------------------------------
# S1 技能表 top3（票 22）：据守 / 贯石斧 / 谦逊
#
# 三条「有条目但太粗」的常量换成**读得出代价**的打分器（spec §6.1 S1）：
#
# - **据守**：已翻面 ⇒ 发动（翻回正面 = 不跳回合，三张纯赚）；正面且 1 体力 ⇒ Pass
#   （跳过下个回合，残局里等于把命交给对手）。
# - **贯石斧**：净收益 = (击杀 / 1 点非致命伤害) − 两张最便宜牌的价 ⇒ 负则 Pass。
#   「两张高价值牌换 1 点非致命伤害」因此是 Pass，「这一下能打死」才发动。
# - **谦逊**：1 体力且手里有能续命的牌（【闪】/【桃】/【酒】）⇒ Pass——把保命牌扣在
#   武将牌上等于等死；其余情况照旧发动（躲开拆顺）。
#
# 先红后绿（spec §2.2）：据守 / 贯石斧 / 谦逊各有一条**修复前必红**的用例，先以
# ``xfail(strict=True)`` 提交；同族的另 7 条是**护栏**（"已翻面 ⇒ 发动""目标 1 体力 ⇒
# 发动（含两张高价值牌的情形）""两张废牌也付得起""只有【桃】也算续命牌"以及三条"别改成
# 永不发动"）：它们修复前也绿，strict 会把 XPASS 判红，故不加标记。
# ---------------------------------------------------------------------------

JUSHSOU = "hero.jie_caoren_mobile.jushou"
"""移动版界曹仁【据守】的全限定技能 id（``SKILL_VALUES`` 的键）。"""

AXE_SKILL = "equip.axe"
"""【贯石斧】的技能 id（``equip.`` + 牌名）：发动询问只在**自己的**【杀】被【闪】抵消时。"""

QIANXUN_SKILL = "hero.jie_luxun.qianxun"
"""界陆逊【谦逊】的全限定技能 id。"""

JIE_CAOREN = "jie_caoren_mobile"
JIE_LUXUN_S1 = "jie_luxun"
"""S1 三条用例要的两个武将 id（技能条目按 ``hero.<id>.<skill>`` 查表）。"""

S1_AI_SEAT = 0
"""据守 / 贯石斧用例的 AI 座位：无身份 2 人局的先手（出牌 / 结束阶段的拥有者）。"""

SECOND_SEAT = 1
"""【谦逊】用例的 AI 座位：**后手**——对手先动，AI 的手牌不会被自己的摸牌 / 弃牌阶段动过。"""

AXE_CARD = cards_of_type(PACK, "axe")[0]
"""装备区里的那张【贯石斧】（``equip.axe`` 实例挂在它上面）。"""

S1_SLASH = cards_of_type(PACK, "slash")[1]
"""S1 用例专用的【杀】``card_id``（避开 D3 的 ``SLASH_ID``，一局里不出现重复实体牌）。"""

S1_PEACH = cards_of_type(PACK, "peach")[6]
S1_DODGE = cards_of_type(PACK, "dodge")[7]
S1_LIGHTNING = cards_of_type(PACK, "lightning")[0]
S1_CHEAP_HORSE = cards_of_type(PACK, "chitu")[0]
S1_DISMANTLEMENT = cards_of_type(PACK, "dismantlement")[0]
"""S1 各用例要的实体牌（按牌名取数据表下标；``桃`` / ``闪`` 取靠后的那张避开别的夹具）。

``S1_LIGHTNING`` + ``S1_CHEAP_HORSE`` 是最便宜的武器：牌价 1.0 的两张（斩杀用例的成本池），
且都不是"出牌阶段会先于【杀】被打出"的动作（【杀】的分数高于装备与延时锦囊）。
"""


def _activation_answer(query, *, skill_id: str):
    """在 ``skill_id`` 的发动询问上取"发动"这一条候选（不手抄 id 以外的形状）。"""
    assert query.kind == "SKILL_ACTIVATION" and query.skill_id == skill_id, (
        f"夹具失效：停在 {query.kind} / {getattr(query, 'skill_id', None)!r}，"
        f"不是 {skill_id} 的发动询问"
    )
    action = find_action(
        query,
        lambda candidate: isinstance(candidate, ActivateSkill)
        and candidate.skill_id == skill_id,
    )
    assert action is not None, f"{skill_id} 的发动询问里没有 ActivateSkill 候选"
    return action


class _AxeDriver:
    """AI（座位 0）的脚本：出牌阶段对座位 1 出这张【杀】，其余取最小合法应答。"""

    def __init__(self) -> None:
        self._fallback = PassPlayer()

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            use = find_action(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == S1_SLASH
                and action.targets == (1,),
            )
            if use is not None:
                return use
        return self._fallback.respond(query)


class _DodgeDriver:
    """对手（座位 1）的脚本：被【杀】时打出一张【闪】（贯石斧的发动前提）。"""

    def __init__(self) -> None:
        self._fallback = PassPlayer()

    def respond(self, query):
        if query.kind == "DODGE":
            dodge = find_action(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.type == "dodge",
            )
            if dodge is not None:
                return dodge
        return self._fallback.respond(query)


def _s1_seat(*, hero=None, hp=4, hp_max=4, hand=(), equip=()):
    return SeatSpec(
        hero=hero, hp=hp, hp_max=hp_max, hand=tuple(hand), equip=tuple(equip)
    )


def _axe_session(*, defender_hp: int, hand, ai_seat=S1_AI_SEAT):
    """推进到**贯石斧的发动询问**：真【杀】被真【闪】抵消之后的那一问。

    与 D2 / C1 同一驱动姿态（真局面 → ``Engine.from_state`` → ``run_queries``）；牌堆顶
    两张惰性【闪】吸收摸牌阶段，AI 的手牌因此 = 构造的手牌 − 【杀】 + 2 张【闪】。
    """
    seats = [
        _s1_seat(hand=hand, equip=(AXE_CARD,)),
        _s1_seat(hp=defender_hp, hand=(cards_of_type(PACK, "dodge")[0],)),
    ]
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=2),
        seats=seats,
        deck=_deck_with_inert_top(PACK, seats=seats, count=2),
        turn_seat=ai_seat,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(
        engine,
        [_AxeDriver(), _DodgeDriver()],
        lambda current: current.pending is not None
        and current.pending.kind == "SKILL_ACTIVATION"
        and current.pending.skill_id == AXE_SKILL,
    )
    query = engine.pending
    assert query is not None, "推进器在到达【贯石斧】的发动询问前就停了"
    return engine, query


def _axe_nonlethal_query():
    """2 人局：AI 的【杀】被抵消，手里只剩【桃】+【闪】（两张高价值牌）⇒ 不值得。

    瞄准的是 4 体力目标（非致命）：付掉桃 / 闪换 1 点伤害是负收益，故 Pass。
    """
    return _axe_session(defender_hp=4, hand=(S1_SLASH, S1_PEACH, S1_DODGE))[1]


def _axe_lethal_query():
    """2 人局：目标 **1 体力**、AI 手里是【闪电】+【赤兔】（牌价 1.0 的两张）⇒ 这一下是斩杀。

    「目标 1 体力 ⇒ 发动」那一半：同样的两张牌在非致命局面下不够看，但能打死人就值。
    """
    return _axe_session(defender_hp=1, hand=(S1_SLASH, S1_LIGHTNING, S1_CHEAP_HORSE))[1]


def _jushou_query(*, hp: int = 1):
    """2 人局：AI = 界曹仁（座位 0）**正面**、给定体力，走到自己的结束阶段 ⇒ 据守的发动询问。

    真流程：``Engine.from_state`` → 摸牌 / 出牌阶段（脚本直接结束阶段）→ 结束阶段发问。
    """
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=2),
        seats=[_s1_seat(hero=JIE_CAOREN, hp=hp, hp_max=4), _s1_seat()],
        deck=deck_excluding(PACK),
        turn_seat=S1_AI_SEAT,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda current: current.pending is not None
        and current.pending.kind == "SKILL_ACTIVATION"
        and current.pending.skill_id == JUSHSOU,
    )
    query = engine.pending
    assert query is not None, "推进器在到达【据守】的发动询问前就停了"
    return engine, query


def _jushou_flipped_query():
    """已翻面的据守询问（**构造询问**，不是引擎产出：见用例 docstring）。

    引擎里唯一的翻面来源就是【据守】自己，而背面朝上的角色在**回合开始前**被翻回正面并
    跳过整个回合（规则集 13.2 / ``Engine._turn_before_start``）⇒ 真实对局走不到"自己回合的
    结束阶段还翻着面"这一格。这一格仍要有判据（日后【放逐】一类会给它真实的第二个来源），
    故按**真投影 + 真询问模型 + 真打分器**构造：``project(state, 0)`` 读出
    ``PlayerPublic.flipped``，候选与 ``Kind`` 与引擎产出同形。
    """
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=2),
        seats=[_s1_seat(hero=JIE_CAOREN, hp=3, hp_max=4), _s1_seat()],
        deck=deck_excluding(PACK),
        turn_seat=S1_AI_SEAT,
    )
    state.players[S1_AI_SEAT].flipped = True
    view = project(state, S1_AI_SEAT)
    seq = 1
    return SkillActivationQuery(
        seq=seq,
        actor=S1_AI_SEAT,
        timing=Timing.PHASE_FINISH,
        prompt="是否发动该技能？",
        legal_actions=(
            ActivateSkill(answers_seq=seq, skill_id=JUSHSOU),
            Pass(answers_seq=seq),
        ),
        view=view,
        skill_id=JUSHSOU,
    )


def _qianxun_query(*, hp: int = 1, hand=(S1_DODGE, S1_PEACH)):
    """2 人局：界陆逊（AI，座位 1）给定体力与手牌，被座位 0 的【过河拆桥】指上。

    **AI 坐后手**（对手先行动）：这样到发动询问时它的手牌还是构造的那几张——先手局里它会先走
    一轮，1 体力的弃牌阶段会把 2 张手牌削到 1 张，夹具就测不到"手里有哪类牌"。
    真流程：座位 0 的回合，真【过河拆桥】指向座位 1 ⇒ 锦囊"对你生效"⇒ 【谦逊】的发动询问。
    ``hand`` 可换（"只有【桃】"那条守卫）。
    """
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=2),
        seats=[
            _s1_seat(hand=(S1_DISMANTLEMENT,)),
            _s1_seat(hero=JIE_LUXUN_S1, hp=hp, hp_max=3, hand=tuple(hand)),
        ],
        deck=deck_excluding(PACK, *hand, S1_DISMANTLEMENT),
        turn_seat=SECOND_SEAT,
    )
    engine = Engine.from_state(state, seed=7)

    class _DismantleDriver:
        """座位 0 的脚本：【过河拆桥】指向座位 1（AI），其余取最小合法应答。"""

        def __init__(self) -> None:
            self._fallback = PassPlayer()

        def respond(self, query):
            if query.kind == "PLAY_PHASE":
                use = find_action(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.card_id == S1_DISMANTLEMENT
                    and action.targets == (SECOND_SEAT,),
                )
                if use is not None:
                    return use
            return self._fallback.respond(query)

    run_queries(
        engine,
        [_DismantleDriver(), PassPlayer()],
        lambda current: current.pending is not None
        and current.pending.kind == "SKILL_ACTIVATION"
        and current.pending.skill_id == QIANXUN_SKILL,
    )
    query = engine.pending
    assert query is not None, "推进器在到达【谦逊】的发动询问前就停了"
    return engine, query


def _assert_activation(query, *, skill_id: str, activate: bool) -> None:
    """跑真启发式：8 个平分子流种子**必须同一个答案**（不许靠抽签过关）。

    先核这一问确实是 ``skill_id`` 的**发动询问**、且候选里有它的 ``ActivateSkill``
    （夹具失效与策略判据分开可见）；再逐种子核答案。
    """
    activation = _activation_answer(query, skill_id=skill_id)
    assert activation.skill_id == skill_id
    for seed in TIE_SEEDS:
        answer = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        if activate:
            assert isinstance(answer, ActivateSkill), (
                f"{skill_id} 该发动，实得 {answer!r}"
            )
            assert answer.skill_id == skill_id
        else:
            assert isinstance(answer, Pass), f"{skill_id} 该 Pass，实得 {answer!r}"


def test_the_skill_table_fixtures_reach_real_windows():
    """夹具自检（不带 xfail）：三条 S1 用例的构造面（询问归属 / 牌面 / 成本池）。

    只核形状，不核策略：夹具被引擎改动弄坏时保持可见，而不是被 strict xfail 一起吞掉。
    """
    _engine, axe = _axe_session(defender_hp=4, hand=(S1_SLASH, S1_PEACH, S1_DODGE))
    assert axe.kind == "SKILL_ACTIVATION" and axe.actor == S1_AI_SEAT
    assert axe.skill_id == AXE_SKILL, "夹具失效：停在的不是【贯石斧】的发动询问"
    assert [card.type for card in axe.view.processing] == ["slash"], (
        "夹具失效：处理区顶牌不是那张被抵消的【杀】"
    )
    assert axe.view.players[S1_AI_SEAT].equip[0].type == "axe", "夹具失效：AI 没装备【贯石斧】"
    assert sorted(card.type for card in axe.view.self_hand) == [
        "dodge",
        "dodge",
        "dodge",
        "peach",
    ], (
        "夹具失效：成本池不是 2 张惰性【闪】+【桃】+【闪】，"
        f"实得 {[card.type for card in axe.view.self_hand]}"
    )

    _engine, jushou = _jushou_query()
    assert jushou.kind == "SKILL_ACTIVATION" and jushou.actor == S1_AI_SEAT
    assert jushou.skill_id == JUSHSOU
    assert jushou.view.players[S1_AI_SEAT].hp == 1
    assert not jushou.view.players[S1_AI_SEAT].flipped, "夹具失效：正面用例里 AI 已经翻面"

    flipped = _jushou_flipped_query()
    assert flipped.view.players[S1_AI_SEAT].flipped, "夹具失效：构造询问里 AI 没有翻面"

    _engine, qianxun = _qianxun_query()
    assert qianxun.kind == "SKILL_ACTIVATION" and qianxun.actor == SECOND_SEAT
    assert qianxun.skill_id == QIANXUN_SKILL
    assert qianxun.view.players[SECOND_SEAT].hp == 1
    assert any(card.type == "dodge" for card in qianxun.view.self_hand), (
        "夹具失效：1 体力的界陆逊手里没有【闪】"
    )
    assert qianxun.view.processing[-1].type == "dismantlement", (
        "夹具失效：处理区顶牌不是【过河拆桥】"
    )


def test_s1_jushou_passes_at_one_hp_when_face_up():
    """据守：**正面**且只剩 1 体力 ⇒ Pass（不浪费下个回合，也不在残局里翻过去）。

    修复前是 ``_constant(2.0)`` ⇒ 8 个种子恒发动（红提交 ab005cf 的 strict xfail）；
    修复后按"翻面的代价"判。
    """
    _engine, query = _jushou_query()
    _assert_activation(query, skill_id=JUSHSOU, activate=False)


def test_s1_jushou_activates_when_already_flipped():
    """据守：**已翻面** ⇒ 发动（这一次翻面是翻回正面，不跳回合，三张牌纯赚）。

    护栏用例（修复前也绿：常量恒发动）；它钉的是新分支——别把"已翻面"一起判成 Pass。
    构造询问的理由见 :func:`_jushou_flipped_query`。
    """
    query = _jushou_flipped_query()
    _assert_activation(query, skill_id=JUSHSOU, activate=True)


def test_s1_axe_passes_when_two_good_cards_buy_one_ordinary_damage():
    """贯石斧：付【桃】+【闪】换 1 点**非致命**伤害 ⇒ Pass（净收益为负）。

    修复前恒发动（弃掉两张好牌换 1 点伤害）；修复后是否发动看"两张最便宜的牌 vs 伤害"。
    """
    query = _axe_nonlethal_query()
    _assert_activation(query, skill_id=AXE_SKILL, activate=False)


def test_s1_axe_activates_when_the_saved_slash_is_lethal():
    """贯石斧：目标 **1 体力** ⇒ 发动（这一下就是击杀，值两张最便宜的牌）。

    护栏用例（修复前也绿）；它钉住"能打死就换"的另一半，防止把斧子改成永不发动。
    """
    query = _axe_lethal_query()
    _assert_activation(query, skill_id=AXE_SKILL, activate=True)


def test_s1_qianxun_passes_at_one_hp_with_a_dodge_in_hand():
    """谦逊：1 体力、手里有【闪】，被【过河拆桥】指上 ⇒ Pass（不把保命牌扣上武将牌）。

    修复前恒发动：把手牌全扣到武将牌上 = 本回合再没有【闪】可打，等于等死；
    修复后 1 体力时只在"手里没有能续命的牌"才发动。
    """
    _engine, query = _qianxun_query()
    _assert_activation(query, skill_id=QIANXUN_SKILL, activate=False)

def test_s1_jushou_activates_when_healthy_and_face_up():
    """护栏：正面、体力够（2 体力以上）⇒ 照旧发动据守（三张牌抵得过一次跳过）。

    修复前也绿（常量恒发动）；它钉住"别把据守一路改到永不发动"——两个 Pass 分支之外，
    正常的据守节奏不能丢。
    """
    _engine, query = _jushou_query(hp=3)
    _assert_activation(query, skill_id=JUSHSOU, activate=True)


def test_s1_qianxun_activates_when_not_at_one_hp():
    """护栏：不残局（3 体力）时照旧发动谦逊（躲开拆 / 顺是净赚）。

    修复前也绿（常量恒发动）；它钉住"1 体力的例外别扩散成永不发动"。
    """
    _engine, query = _qianxun_query(hp=3)
    _assert_activation(query, skill_id=QIANXUN_SKILL, activate=True)


def test_s1_axe_activates_when_the_two_cards_are_nearly_worthless():
    """护栏：付两张几乎没用的牌（【闪电】+【赤兔】，各 1.0）换 1 点非致命伤害 ⇒ 发动。

    修复前也绿（常量恒发动）；它从另一侧钉住成本模型——非致伤害是否值得，取决于**具体
    那两张牌**，不是"斧子一律 Pass"。
    """
    _engine, query = _axe_session(
        defender_hp=4, hand=(S1_SLASH, S1_LIGHTNING, S1_CHEAP_HORSE)
    )
    _assert_activation(query, skill_id=AXE_SKILL, activate=True)

def test_s1_axe_activates_when_the_lethal_target_is_bought_with_good_cards():
    """护栏：目标 1 体力**就是**斩杀，哪怕要付两张好牌（【桃】+【闪】）也发动。

    票面验收写的是「目标 1 体力 ⇒ 发动」，没有给斩杀加成本条件（与 D2 的斩杀项同口径：
    能打死就不为两张牌放弃）。它钉住"击杀不扣牌价"——否则这条会退回成 Pass。
    """
    query = _axe_session(defender_hp=1, hand=(S1_SLASH, S1_PEACH, S1_DODGE))[1]
    _assert_activation(query, skill_id=AXE_SKILL, activate=True)


def test_s1_qianxun_passes_at_one_hp_with_only_a_peach():
    """护栏：1 体力、手里没有【闪】但有【桃】⇒ 同样 Pass（续命牌是闭集，不只看【闪】）。

    票面只点名【闪】；【桃】/【酒】是"1 体力时扣在武将牌上同样用不出来"的同一条语义成员
    （`_SELF_PRESERVATION_CARD_TYPES`），这条守卫钉住闭集里【桃】那一格。
    """
    _engine, query = _qianxun_query(hand=(S1_PEACH, S1_PEACH))
    _assert_activation(query, skill_id=QIANXUN_SKILL, activate=False)


# ---------------------------------------------------------------------------
# D4 装备先手（票 25）：装上之后这一刀才"打得死" ⇒ 先装，再杀
# ---------------------------------------------------------------------------


SCIMITAR_160 = cards_of_type(PACK_160, "ancient_scimitar")[0]
"""【古锭刀】（只在军争 160）：目标**没有手牌**时你的【杀】伤害 +1。"""

QINGGANG_160 = cards_of_type(PACK_160, "qinggang_sword")[0]
"""【青釭剑】：无视防具（普通【杀】打【藤甲】不再被挡成 0）。"""


def _setup_seats(*, hand, target_hp, target_hand=(), target_equip=()):
    """无身份 2 人局：AI = 座位 0（先手）、握 ``hand``；座位 1 是**唯一**的目标。

    无身份 ⇒ 关系项为 0，判据只剩"这一刀打不打得死"，不被"打谁是敌人"干扰；
    目标只有一个 ⇒ 候选面里没有第二个目标可分心（与 D2 的 ``_wine_slash_seats`` 同形）。
    """
    return [
        SeatSpec(hp=4, hp_max=4, hand=hand),
        SeatSpec(
            hp=target_hp, hp_max=4, hand=tuple(target_hand), equip=tuple(target_equip)
        ),
    ]


def _setup_session(*, hand, target_hp, target_hand=(), target_equip=()):
    """推进到 AI（座位 0）的第一个出牌阶段；返回 ``(engine, query)``。

    两步链要**真的把装备提交进引擎**（见
    :func:`test_d4_equips_the_scimitar_before_the_lethal_slash_it_unlocks`）。
    """
    return _lethal_play_session(
        pack=PACK_160,
        seats=_setup_seats(
            hand=hand,
            target_hp=target_hp,
            target_hand=target_hand,
            target_equip=target_equip,
        ),
        ai_seat=0,
        inert=2,
    )


def _scimitar_query():
    """手里【古锭刀】+【杀】，目标 2 体力 **0 手**：装上刀这一刀才是 2 点（斩杀）。

    不装刀 = 1 点 ⇒ 打不死；装上 = 1 + 1（古锭刀的"目标没有手牌"）= 2 点 ⇒ 打死。
    """
    return _setup_session(hand=(SCIMITAR_160, SLASH_160), target_hp=2)[1]


def _qinggang_query():
    """手里【青釭剑】+【杀】，目标 1 体力穿【藤甲】：现在这一刀被防具挡成 0 点。

    装上青釭剑（无视防具）⇒ 1 点 ⇒ 正好打死。它**修复前已绿**（不装时这一刀被
    ``_WASTED_ATTACK_PENALTY`` 压在装备分之下），故是护栏而不是 xfail 判据。
    """
    return _setup_session(
        hand=(QINGGANG_160, SLASH_160), target_hp=1, target_equip=(VINE_ARMOUR_160,)
    )[1]


def _unrelated_setup_query():
    """手里【古锭刀】+【杀】，目标 2 体力 **1 手**：古锭刀的 +1 不生效，装上也不改斩杀。

    它是"窄面"的对照面：装备分必须**逐值不变**（不因为"手里有刀"就抢在【杀】前面）。
    """
    return _setup_session(
        hand=(SCIMITAR_160, SLASH_160),
        target_hp=2,
        target_hand=(cards_of_type(PACK, "dodge")[4],),
    )[1]


QILIN_BOW_160 = cards_of_type(PACK_160, "qilin_bow")[0]
"""【麒麟弓】（射程 5）：装它会**换下**手里那把【古锭刀】（射程 2）。"""


def _shorter_weapon_query():
    """AI 已装【麒麟弓】(射程 5)、手里【古锭刀】(射程 2) + 【杀】，唯一可斩的目标在**距离 3**。

    6 人局座位 0 = AI（主公）；座位 3 是 2 体力 0 手的目标，与 AI 相隔 3 席（射程边缘）。
    装上【古锭刀】之后这一刀确实够 2 点（斩杀），但射程从 5 掉到 2 ⇒ **座位 3 直接掉出
    【杀】的候选面**。判据必须问的不只是"伤害够不够"，还有"装完之后那个目标还够得着吗"。
    """
    return _lethal_play_query(
        pack=PACK_160,
        seats=[
            SeatSpec(
                hp=4,
                hp_max=4,
                identity=Identity.LORD,
                hand=(SCIMITAR_160, SLASH_160),
                equip=(QILIN_BOW_160,),
            ),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LOYALIST),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=2, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hp_max=4, identity=Identity.RENEGADE),
        ],
        ai_seat=0,
        inert=2,
    )


def _equip_action(query, card_id: int):
    """候选面里"使用这张装备牌"的那一条（装备的使用 = ``UseCard``，目标是自己）。"""
    return find_action(
        query,
        lambda action: isinstance(action, UseCard) and action.card.card_id == card_id,
    )


def _slash_use(query):
    """候选面里出【杀】的那一条（单目标；本节的局面只有一个目标）。"""
    return find_action(
        query,
        lambda action: isinstance(action, UseCard) and is_slash(behavior_for(action.card)),
    )


def test_the_equipment_setup_fixtures_reach_real_play_phase_windows():
    """夹具自检（不带 xfail）：停下来的真是 AI 的出牌阶段，且两张关键牌都在候选面上。

    只核**形状**：询归属、装备与【杀】都在候选里、目标体力 / 手牌 / 防具与构造一致。
    不核"该先出哪张"——那是下方判据的事；夹具被引擎改动弄坏时保持可见。
    """
    scimitar = _scimitar_query()
    assert scimitar.kind == "PLAY_PHASE" and scimitar.actor == 0
    assert _equip_action(scimitar, SCIMITAR_160) is not None, "夹具失效：古锭刀不在候选里"
    (target,) = _slash_targets(scimitar)
    assert (
        scimitar.view.players[target].hp,
        scimitar.view.players[target].hand_count,
    ) == (2, 0), "夹具失效：目标不再是「2 体力 0 手」"

    qinggang = _qinggang_query()
    assert qinggang.kind == "PLAY_PHASE" and qinggang.actor == 0
    assert _equip_action(qinggang, QINGGANG_160) is not None, "夹具失效：青釭剑不在候选里"
    (qinggang_target,) = _slash_targets(qinggang)
    assert qinggang.view.players[qinggang_target].hp == 1, "夹具失效：目标不再在 1 体力"
    assert any(
        card.type == "vine_armor"
        for card in qinggang.view.players[qinggang_target].equip
    ), "夹具失效：目标不再穿【藤甲】"

    unrelated = _unrelated_setup_query()
    (unrelated_target,) = _slash_targets(unrelated)
    assert (
        unrelated.view.players[unrelated_target].hp,
        unrelated.view.players[unrelated_target].hand_count,
    ) == (2, 1), "夹具失效：目标不再是「2 体力 1 手」"

    for query in (scimitar, qinggang, unrelated):
        assert _slash_use(query) is not None, "夹具失效：出牌阶段没有【杀】的候选"
        for seed in TIE_SEEDS:
            action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
            assert action in query.legal_actions, "AI 应答必须落在真实候选面上"


def _assert_equips_then_strikes(*, session, equipment: int, why: str) -> None:
    """两步链的共用断言：先使用 ``equipment``，**再**把【杀】落到同一个目标（真提交两次）。

    只断**顺序与落点**（§4：不比集合、不先归一化）；"分是多少"归各自的 docstring，
    这里只回答"这一手先装、下一手补刀同一座位"。``why`` 是首步失败的**判据说明**，
    两次失败各有各的因果，不让共用模板把它们说成一句话。
    """
    for seed in TIE_SEEDS:
        engine, query = session()
        (target,) = _slash_targets(query)
        player = HeuristicPlayer(ai_stream(seed, query.actor))

        first = player.respond(query)

        assert isinstance(first, UseCard) and first.card.card_id == equipment, (
            f"{why}，实得 {first!r}"
        )
        assert first.targets == (query.actor,), "装备的使用目标只可能是自己"

        engine.submit(first)
        run_queries(
            engine,
            [player, *(PassPlayer() for _ in range(len(query.view.players) - 1))],
            lambda current: current.pending is not None
            and current.pending.kind == "PLAY_PHASE"
            and current.pending.actor == query.actor,
        )

        second = player.respond(engine.pending)

        assert isinstance(second, UseCard) and is_slash(behavior_for(second.card)), (
            f"装完之后该补【杀】，实得 {second!r}"
        )
        assert second.targets == (target,), (
            f"【杀】该落在同一目标（座位 {target}），实得 {second.targets}"
        )


def test_d4_equips_the_scimitar_before_the_lethal_slash_it_unlocks():
    """D4 装备先手：目标 2 体力 0 手 ⇒ **先装【古锭刀】**，再补那一刀（两步都真的提交）。

    第一刀（不装刀）只有 1 点 ⇒ 打不死；装上刀之后同一张【杀】是 2 点 ⇒ 当场打死。
    修复前装备恒为 ``_EQUIP_PLAY_VALUE``(2.5)，那条【杀】按"1 点期望伤害"得 4.0 ⇒ AI
    **先打后装**，把残局放跑（seed 恒定，8 个平分子流全同一选择）。
    """
    _assert_equips_then_strikes(
        session=lambda: _setup_session(hand=(SCIMITAR_160, SLASH_160), target_hp=2),
        equipment=SCIMITAR_160,
        why="目标 2 体力 0 手：装上【古锭刀】这一刀才是 2 点（斩杀）",
    )


def test_d4_equips_qinggang_before_the_slash_vine_armour_would_swallow():
    """D4 装备先手（**修复前已绿**，护栏）：普通【杀】被【藤甲】挡成 0 ⇒ 先装【青釭剑】。

    票面允许"朱雀 / 青釭若现有打分已会先装，就只留古锭刀用例"；这条仍保留，因为它是
    唯一能钉住**两步链完整**的护栏：被挡成 0 的【杀】本来就被判成浪费（低于装备分），
    装完青釭剑之后同一张【杀】才是斩杀。装配分的加法不许把这条改成"先杀后装"。
    """
    _assert_equips_then_strikes(
        session=lambda: _setup_session(
            hand=(QINGGANG_160, SLASH_160),
            target_hp=1,
            target_equip=(VINE_ARMOUR_160,),
        ),
        equipment=QINGGANG_160,
        why="普通【杀】打【藤甲】是 0 点 ⇒ 应先装【青釭剑】（无视防具）",
    )


def test_d4_leaves_the_equipment_score_alone_when_the_kill_does_not_depend_on_it():
    """D4 窄面（**修复前已绿**，护栏）：装上也不改斩杀 ⇒ 装备分**逐值不变**，照旧先杀。

    票面写的"只在先装后杀能把打不死变成打死时"就是这条：目标还有 1 张手牌 ⇒ 古锭刀的
    +1 不生效 ⇒ 装与不装都只有 1 点 ⇒ 没有 setup 分可加。把装备分无差别抬高会把
    "打一刀再说"改成"先摆装备"，是这条用例要挡住的那一格。

    **断到分数**而不只断"先出【杀】"：装备分被微抬到仍在【杀】之下（例如 2.6）时，
    动作级断言照样绿，而"逐值不变"已经不成立——这条的题目要求的就是那一个字面值。
    """
    query = _unrelated_setup_query()
    equipment = _equip_action(query, SCIMITAR_160)
    assert equipment is not None, "夹具失效：古锭刀不在候选里"
    assert (
        heuristic_scoring._score_play_phase(query, equipment)
        == heuristic_scoring._EQUIP_PLAY_VALUE
    ), "装上不改斩杀 ⇒ 装备分必须逐值等于 _EQUIP_PLAY_VALUE"
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert isinstance(action, UseCard) and is_slash(behavior_for(action.card)), (
            f"装上不改斩杀时应当先出【杀】，实得 {action!r}"
        )


def test_d4_does_not_swap_to_a_shorter_weapon_for_a_target_it_would_lose():
    """D4 窄面（评审 B2 的回归）：装完够不着 ⇒ 不算"先装后杀能把打不死变成打死"。

    局面见 :func:`_shorter_weapon_query`：已装【麒麟弓】(射程 5)、手里【古锭刀】(射程 2)、
    唯一可斩的目标在**距离 3**。只看伤害的实现会把装备分抬到 5.7（高于全部【杀】的 5.6）
    ⇒ 先装刀；装完射程 2，那个目标再也不是【杀】的合法目标 ⇒ **白装一手**，斩杀反而没了。
    判据因此还要问"装完之后射程会不会缩短"：会 ⇒ 不追加分，照旧先出这一刀。
    """
    query = _shorter_weapon_query()
    for seed in TIE_SEEDS:
        action = HeuristicPlayer(ai_stream(seed, query.actor)).respond(query)
        assert not (
            isinstance(action, UseCard) and action.card.card_id == SCIMITAR_160
        ), f"换成射程更短的刀会把目标弄丢 ⇒ 不该先装，实得 {action!r}"
        assert isinstance(action, UseCard) and is_slash(behavior_for(action.card)), (
            f"应当先出够得着的【杀】，实得 {action!r}"
        )


# ---------------------------------------------------------------------------
# S2 技能表（feng-xiahouyuan 票 01）：夏侯渊【神速】只在该补刀时发动
#
# 判据来自 :func:`sgs.agents.heuristic._score_shensu`：跳过摸牌阶段要少摸两张、只换一张
# 【杀】，常态不划算；只有"敌方能被这一张【杀】打死"（hp ≤ 1、且敌方概率过半）才值得。
# 两局只差敌方那 3 点体力，答案必须相反——否则就是"每回合自废摸牌"的那类蠢。
# ---------------------------------------------------------------------------

SHENSU_SKILL = "hero.xiahouyuan.shensu"
SHENSU_AI_SEAT = 0
"""本构造局面里 AI 的座位：主公（身份对本人可见，故能算出敌我概率）。"""

SHENSU_ENEMY_SEAT = 1


def _shensu_query(*, enemy_hp: int):
    """造「座位 0 = 主公夏侯渊、座位 1 = 反贼」的回合开始，停在【神速】的发动询问。"""
    hand = tuple(cards_of_type(PACK, "dodge")[:2])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="xiahouyuan", identity=Identity.LORD, hand=hand),
            SeatSpec(hp=enemy_hp, identity=Identity.REBEL),
            SeatSpec(identity=Identity.LOYALIST),
            SeatSpec(identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, *hand),
        turn_seat=SHENSU_AI_SEAT,
        phase=Phase.PREPARE,
        rule_config=RuleConfig(player_count=4),
    )
    engine = Engine.from_state(state, seed=2026)
    run_queries(
        engine,
        [PassPlayer() for _ in range(4)],
        lambda current: current.pending is not None
        and current.pending.kind == "SKILL_ACTIVATION"
        and current.pending.skill_id == SHENSU_SKILL,
    )
    query = engine.pending
    assert query is not None, "推进器在到达【神速】的发动询问前就停了"
    return engine, query


def test_the_shensu_fixture_reaches_a_real_turn_start_window():
    """夹具自检：这一问确实是【神速】的发动询问、问的是主公自己、敌方只剩 1 体力。"""
    _engine, query = _shensu_query(enemy_hp=1)

    assert query.kind == "SKILL_ACTIVATION"
    assert query.actor == SHENSU_AI_SEAT
    assert query.skill_id == SHENSU_SKILL
    assert query.view.players[SHENSU_ENEMY_SEAT].hp == 1
    assert query.view.players[SHENSU_AI_SEAT].identity == Identity.LORD.value


def test_s2_shensu_activates_when_the_slash_can_finish_off_an_enemy():
    """敌方 1 体力 ⇒ 发动（这一张无距离【杀】能补刀）。"""
    _engine, query = _shensu_query(enemy_hp=1)
    _assert_activation(query, skill_id=SHENSU_SKILL, activate=True)


def test_s2_shensu_passes_when_the_slash_cannot_finish_anyone():
    """敌方满体力 ⇒ Pass（为一张【杀】放弃摸牌阶段的两张牌不划算）。"""
    _engine, query = _shensu_query(enemy_hp=4)
    _assert_activation(query, skill_id=SHENSU_SKILL, activate=False)


# ---------------------------------------------------------------------------
# huo-dianwei-mobile 票 01：典韦移动版【强袭】的三步询问（发动 / 代价 / 目标）
#
# 判据只读公开面：有武器牌可弃 ⇒ 弃牌比失去 1 点体力便宜（−2.0 > −3.5）；座位步按敌我
# 关系挑人（座位 1 = 反贼、2 / 3 = 忠臣）——不登记 `_HOSTILE_SEAT_STEPS` 时 AI 会打忠臣。
#
# 本技能**不翻预发动**（骨架仍在 legal_actions），但引擎对每个候选技能都渲染一份清单，
# 故 AI 走的是"填清单 + 一次提交"的**复合动作**（`choices` 里带着代价选择）——这正是
# 界面上真人点技能后再选代价的同一份声明，两档走的都是同一条 `SkillFlow`。
# ---------------------------------------------------------------------------

QIANGXI_SKILL = "hero.dianwei_mobile.qiangxi"
QIANGXI_SEAT = 0
QIANGXI_ENEMY_SEAT = 1


def _qiangxi_engine(*, hp: int = 4, equip=(), reveal_enemy: bool = False):
    """座位 0 = 主公典韦移动版（出牌阶段），1 = 反贼，2 / 3 = 忠臣。

    牌堆顶两张【闪】：摸牌阶段摸到也用不出去，出牌阶段的候选于是只剩【强袭】（与结束
    出牌阶段）——被测的决策不被别的牌抢走。
    """
    dodges = cards_of_type(PACK, "dodge")[:2]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hero="dianwei_mobile",
                hp=hp,
                hp_max=4,
                identity=Identity.LORD,
                equip=equip,
            ),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.LOYALIST),
            SeatSpec(identity=Identity.LOYALIST),
        ],
        deck=(*dodges, *deck_excluding(PACK, *equip, *dodges)),
        turn_seat=QIANGXI_SEAT,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4),
    )
    if reveal_enemy:
        # 身份局里别人的身份默认不可见（`PlayerView` 只揭示本人 / 主公 / 已死亡者），
        # 这里让反贼**亮明身份**，座位步的敌我才分得出来（"已亮明 ⇒ 精确 0/1"那一档）。
        state.player(QIANGXI_ENEMY_SEAT).identity_visible = True
    return Engine.from_state(state, seed=2026)


def _qiangxi_play_query(engine):
    """推进到"出牌阶段询问里真有【强袭】候选"的那一问。"""
    run_queries(
        engine,
        [PassPlayer() for _ in range(4)],
        lambda current: current.pending is not None
        and current.pending.kind == "PLAY_PHASE"
        and find_action(
            current.pending,
            lambda action: isinstance(action, ActivateSkill)
            and action.skill_id == QIANGXI_SKILL,
        )
        is not None,
    )
    query = engine.pending
    assert query is not None, "推进器在到达【强袭】的候选前就停了"
    return query


def _step_until(engine, predicate, *, limit: int = 40):
    """用真启发式应答，把引擎推到下一个满足 ``predicate(query)`` 的询问。

    与 ``support.run_queries`` 同一推进姿态：``submit`` 之后引擎要再 ``_step()`` 一次才会
    摆出下一个询问（``pending`` 在两次之间是 ``None``）。
    """
    for _ in range(limit):
        if engine.pending is None:
            engine._step()
            continue
        if predicate(engine.pending):
            return engine.pending
        engine.submit(
            HeuristicPlayer(ai_stream(1, engine.pending.actor)).respond(engine.pending)
        )
    raise AssertionError("步数上限内没有到达目标询问")


def test_the_qiangxi_fixture_reaches_a_real_play_phase_window():
    """夹具自检：这一问是出牌阶段、问的是典韦自己、候选里真有【强袭】。"""
    engine = _qiangxi_engine(equip=(cards_of_type(PACK, "crossbow")[0],))
    query = _qiangxi_play_query(engine)

    assert query.kind == "PLAY_PHASE"
    assert query.actor == QIANGXI_SEAT
    assert {action.kind for action in query.legal_actions} == {"ActivateSkill", "EndPhase"}, (
        "手里两张【闪】用不出去 ⇒ 只剩【强袭】与结束出牌阶段"
    )


def test_s3_qiangxi_activates_with_a_weapon_card_in_hand_or_equipped():
    """有武器牌可弃 ⇒ 发动（弃牌比失去 1 点体力便宜，1 点伤害净赚）。"""
    engine = _qiangxi_engine(equip=(cards_of_type(PACK, "crossbow")[0],))
    query = _qiangxi_play_query(engine)

    for seed in TIE_SEEDS:
        answer = HeuristicPlayer(ai_stream(seed, QIANGXI_SEAT)).respond(query)
        assert isinstance(answer, ActivateSkill), f"该发动【强袭】，实得 {answer!r}"
        assert answer.skill_id == QIANGXI_SKILL


def test_s3_qiangxi_prefills_the_cheaper_cost_discard_the_weapon():
    """清单里的代价项：弃武器（−2.0）比失去 1 点体力（−3.5）便宜 ⇒ 预填前者。"""
    engine = _qiangxi_engine(equip=(cards_of_type(PACK, "crossbow")[0],))
    query = _qiangxi_play_query(engine)

    for seed in TIE_SEEDS:
        answer = HeuristicPlayer(ai_stream(seed, QIANGXI_SEAT)).respond(query)
        assert isinstance(answer, ActivateSkill)
        assert tuple(answer.choices) == ("qiangxi_discard_weapon",), (
            f"该弃武器，实得 {answer.choices!r}"
        )


def test_s3_qiangxi_passes_when_losing_the_last_hp_would_put_it_into_dying():
    """1 体力且没有武器牌 ⇒ 不发动（自伤会把自己送进濒死）。"""
    engine = _qiangxi_engine(hp=1)
    query = _qiangxi_play_query(engine)

    for seed in TIE_SEEDS:
        answer = HeuristicPlayer(ai_stream(seed, QIANGXI_SEAT)).respond(query)
        assert not (
            isinstance(answer, ActivateSkill) and answer.skill_id == QIANGXI_SKILL
        ), f"1 体力时不该自伤发动，实得 {answer!r}"


def test_s3_qiangxi_targets_the_enemy_not_the_ally():
    """座位步：只按敌我关系挑人（反贼 vs 忠臣），不落进"帮盟友"的缺省分支。"""
    engine = _qiangxi_engine(
        equip=(cards_of_type(PACK, "crossbow")[0],), reveal_enemy=True
    )
    _qiangxi_play_query(engine)
    # 复合动作（代价已预填）→ 弃哪张武器 → 选目标。
    _step_until(engine, lambda query: query.kind == "SELECT_CARDS")
    engine.submit(HeuristicPlayer(ai_stream(1, QIANGXI_SEAT)).respond(engine.pending))
    target_query = _step_until(engine, lambda query: query.kind == "SELECT_OPTION")

    for seed in TIE_SEEDS:
        answer = HeuristicPlayer(ai_stream(seed, QIANGXI_SEAT)).respond(target_query)
        assert isinstance(answer, SelectOption), f"目标步该给选项，实得 {answer!r}"
        assert answer.choice == f"seat:{QIANGXI_ENEMY_SEAT}", (
            f"该打反贼（座位 {QIANGXI_ENEMY_SEAT}），实得 {answer.choice!r}"
        )


# ---------------------------------------------------------------------------
# 界黄忠移动版【烈弓】（jie-feng-batch 票 01）：AI 在触发问上答「发动」
# ---------------------------------------------------------------------------

JIE_LIEGONG = "hero.jie_huangzhong_mobile.liegong"
JIE_HUANGZHONG = "jie_huangzhong_mobile"


def test_jie_liegong_ai_activates_the_trigger_without_cost() -> None:
    """无成本的正收益触发问 ⇒ AI 必须答发动（SKILL_VALUES 常量为正，不靠抽签）。"""
    slash = cards_of_type(PACK, "slash")[0]
    peach = cards_of_type(PACK, "peach")[0]
    dodge = cards_of_type(PACK, "dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_HUANGZHONG), SeatSpec(hp=3)],
        deck=deck_excluding(PACK, slash, peach, dodge),
        turn_seat=0,
    )

    class Script:
        def __init__(self) -> None:
            self.played = False
            self.answer = None

        def respond(self, query):
            if query.kind == "PLAY_PHASE" and not self.played:
                action = find_action(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and tuple(action.targets) == (1,),
                )
                if action is not None:
                    self.played = True
                    return action
                return find_action(query, lambda action: isinstance(action, EndPhase)) or (
                    query.legal_actions[0]
                )
            if query.kind == "SKILL_ACTIVATION" and query.skill_id == JIE_LIEGONG:
                self.answer = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
                return self.answer
            if query.kind == "PLAY_PHASE":
                return find_action(query, lambda action: isinstance(action, EndPhase)) or (
                    query.legal_actions[0]
                )
            return find_action(query, lambda action: isinstance(action, Pass)) or (
                query.legal_actions[0]
            )

    script = Script()
    engine = Engine.from_state(state, seed=1)
    players = [script, PassPlayer()]
    run_queries(
        engine,
        players,
        lambda running: any(
            record.kind == "timing"
            and record.timing is Timing.PHASE_PLAY_START
            for record in running.records
        ),
    )
    owner = engine.state.player(0)
    owner.hand[:] = [slash, peach]
    owner.hp = 4
    target = engine.state.player(1)
    target.hand[:] = [dodge]
    target.hp = 3
    run_queries(
        engine,
        players,
        lambda running: slash in running.state.discard and not running.stack,
    )

    assert isinstance(script.answer, ActivateSkill), f"AI 该答发动，实得 {script.answer!r}"
    assert script.answer.skill_id == JIE_LIEGONG
    assert engine.state.player(1).hp == 2, "封【闪】+ 1 点伤害"


JIE_QIMOU = "hero.jie_weiyan_mobile.qimou"
JIE_WEIYAN = "jie_weiyan_mobile"


def test_jie_qimou_ai_activates_and_takes_the_smallest_x() -> None:
    """界魏延移动版【奇谋】：AI 会发动，并在 X 的中文选项里取最小的 1（不随机送死）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_WEIYAN, hp=4), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: any(
            record.kind == "timing"
            and record.timing is Timing.PHASE_PLAY_START
            for record in running.records
        ),
    )
    # 手牌清空 ⇒ 出牌阶段只剩【奇谋】与「结束出牌阶段」，AI 的选择无歧义。
    engine.state.player(0).hand.clear()
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: running.pending is not None
        and running.pending.kind == "PLAY_PHASE",
    )
    play = engine.pending
    action = HeuristicPlayer(ai_stream(0, play.actor)).respond(play)
    assert isinstance(action, ActivateSkill) and action.skill_id == JIE_QIMOU, (
        f"AI 该发动【奇谋】，实得 {action!r}"
    )
    # AI 可能把 X 一并填进复合动作（预发动清单形态），也可能分两步问；两条路都接受，
    # 判据只看"它选了最小 X"的结果。
    assert not action.choices or action.choices == ("失去1点体力",), (
        f"AI 该取最小 X=1，实得 {action.choices!r}"
    )
    engine.submit(action)
    for _ in range(40):
        if engine.state.player(0).qimou_distance_x:
            break
        if engine.pending is None:
            engine._step()
        else:
            engine.submit(PassPlayer().respond(engine.pending))

    assert engine.state.player(0).hp == 3
    assert engine.state.player(0).qimou_distance_x == 1


# ---------------------------------------------------------------------------
# 陆抗移动版（lukang-mobile 票 01）：【决堰】的栏选择 / ☆【怀柔】的重铸
# ---------------------------------------------------------------------------

LUKANG_MOBILE = "lukang_mobile"
LUKANG_DUANYAN = "hero.lukang_mobile.duanyan"
LUKANG_HUAJROU = "hero.lukang_mobile.huairou"


def test_lukang_duanyan_ai_picks_the_armor_column() -> None:
    """【决堰】：AI 会发动，并在三栏里挑**摸三张 + 手牌上限**那一栏（`_OPTION_VALUES` 最高）。"""
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=LUKANG_MOBILE, equip=(armor,)), SeatSpec()],
        deck=tuple(
            card_id
            for card_id in range(len(PACK.deck.cards))
            if card_id != armor
        ),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: any(
            record.kind == "timing"
            and record.timing is Timing.PHASE_PLAY_START
            for record in running.records
        ),
    )
    # 手牌清空 ⇒ 出牌阶段只剩【决堰】与「结束出牌阶段」，AI 的选择无歧义。
    engine.state.player(0).hand.clear()
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: running.pending is not None
        and running.pending.kind == "PLAY_PHASE",
    )
    play = engine.pending
    action = HeuristicPlayer(ai_stream(0, play.actor)).respond(play)
    assert isinstance(action, ActivateSkill) and action.skill_id == LUKANG_DUANYAN, (
        f"AI 该发动【决堰】，实得 {action!r}"
    )
    engine.submit(action)
    if action.choices:
        # 常见形态：AI 从预发动清单里把选项一并填进复合动作。
        assert action.choices == ("duanyan_armor",), (
            f"AI 该挑『防具栏』（摸三张 + 手牌上限），实得 {action.choices!r}"
        )
    else:
        # 兜底形态：引擎逐步问，AI 再答一次（不交给脚本玩家，它会一并答掉）。
        while engine.pending is None and not engine.finished:
            engine._step()
        assert engine.pending is not None and engine.pending.kind == "SELECT_OPTION", (
            f"发动后该问「废除哪一种装备栏」，实得 {engine.pending!r}"
        )
        option = HeuristicPlayer(ai_stream(0, engine.pending.actor)).respond(engine.pending)
        assert getattr(option, "choice", None) == "duanyan_armor", (
            f"AI 该挑『防具栏』（摸三张 + 手牌上限），实得 {option!r}"
        )
        engine.submit(option)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: "armor" in running.state.player(0).sealed_zones,
    )
    assert "armor" in engine.state.player(0).sealed_zones


def test_lukang_huairou_ai_reforges_an_equipment_card() -> None:
    """☆【怀柔】：AI 会填预发动清单（选那张装备牌）并完成重铸。

    装备牌放在**装备区**（不在手牌）：否则 AI 会先考虑"把【八卦阵】装备上"这条更直接的路。
    """
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=LUKANG_MOBILE, equip=(armor,)), SeatSpec()],
        deck=tuple(
            card_id
            for card_id in range(len(PACK.deck.cards))
            if card_id != armor
        ),
        turn_seat=0,
    )
    state.player(0).granted_skills.append(LUKANG_HUAJROU)
    # 觉醒后的真实局面：【决堰】已被【破势】失去（否则 AI 会先挑决堰——它的分值更高）。
    state.player(0).lost_skills.append(LUKANG_DUANYAN)
    engine = Engine.from_state(state, seed=1)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: any(
            record.kind == "timing"
            and record.timing is Timing.PHASE_PLAY_START
            for record in running.records
        ),
    )
    engine.state.player(0).hand.clear()
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: running.pending is not None
        and running.pending.kind == "PLAY_PHASE",
    )
    play = engine.pending
    action = HeuristicPlayer(ai_stream(0, play.actor)).respond(play)
    assert isinstance(action, ActivateSkill) and action.skill_id == LUKANG_HUAJROU, (
        f"AI 该发动☆【怀柔】，实得 {action!r}"
    )
    assert tuple(action.picks) == (armor,), f"清单要填那一张装备牌，实得 {action.picks!r}"
    engine.submit(action)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: armor in running.state.discard,
    )
    assert armor in engine.state.discard


# ---------------------------------------------------------------------------
# 林包·移动版·神曹操（lin-batch 票 01）：【归心】在"已背面朝上"时照发动
# ---------------------------------------------------------------------------

SHEN_CAOCAO = "shen_caocao_mobile"
GUIXIN = "hero.shen_caocao_mobile.guixin"


def test_shen_caocao_guixin_ai_activates_while_already_back_facing() -> None:
    """【归心】：已背面朝上时这一手是**翻回正面**的收益 ⇒ AI 照发动，并把每座的牌点走。

    这是票面 §A 第 6 格点名的"不犯已知的蠢"用例（`SKILL_VALUES` 的 `_score_guixin`：
    背面朝上 ⇒ ``gains + 1.0``，恒为正）。判据两条：AI 在「是否发动」上答
    ``ActivateSkill``（不是 ``Pass``）；**每一个**座位的 ``SELECT_CARDS`` 上都给出非空
    ``picks``（每座恰好 1 张，Q2 = A 没有逐座放弃出口）。
    """
    channel = cards_of_type(PACK, "slash")[5]
    slash = cards_of_type(PACK, "slash")[1]
    dodge = cards_of_type(PACK, "dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=SHEN_CAOCAO, hp=3, hp_max=3),
            SeatSpec(hand=(slash, dodge)),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, channel, slash, dodge),
        processing=(channel,),
        turn_seat=0,
    )
    state.player(0).flipped = True
    engine = Engine.from_state(state, seed=1)
    channel_ref = state.card(channel)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 1,
            "targets": (0,),
            "card": channel_ref,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 0,
            "channel": channel_ref,
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )

    activation = None
    picks: list = []
    for _ in range(2_000):
        if ran_timing(engine, Timing.DAMAGE_END):
            break
        query = engine.pending
        if query is None:
            engine._step()
            continue
        if query.kind == "SKILL_ACTIVATION" and str(query.skill_id) == GUIXIN:
            activation = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
            engine.submit(activation)
            continue
        if query.kind == "SELECT_CARDS":
            action = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
            picks.append(action)
            engine.submit(action)
            continue
        engine.submit(PassPlayer().respond(query))

    assert isinstance(activation, ActivateSkill) and activation.skill_id == GUIXIN, (
        f"AI 该发动【归心】（背面朝上时可翻回正面），实得 {activation!r}"
    )
    assert len(picks) == 1, f"有牌的座位各一问答，实得 {len(picks)}"
    assert all(tuple(action.picks) for action in picks), "每座都要交出一张"
    assert len(engine.state.player(0).hand) == 1, "拿到的牌进了神曹操的手牌"
    assert engine.state.player(0).flipped is False, "这一手把他翻回正面"


# ---------------------------------------------------------------------------
# 林包·移动版·孙坚（lin-batch 票 02）：【英魂】指向敌人、选【2】（Q6 = A 的两半）
# ---------------------------------------------------------------------------

SUNJIAN_MOBILE = "sunjian_mobile"
YINGHUN = "hero.sunjian_mobile.yinghun"
YINGHUN_DRAW_X_DISCARD_ONE = "yinghun_draw_x_discard_one"
YINGHUN_DRAW_ONE_DISCARD_X = "yinghun_draw_one_discard_x"


def test_sunjian_yinghun_ai_aims_at_the_enemy_and_denies_cards() -> None:
    """【英魂】：座位步指向**已明的敌人**、选项挑【2】（摸 1 张、然后其弃置 X 张）。

    这是票面 §AI 适配第 6 格点名的确定性用例，钉 Q6 = A 的两半：

    - **座位步**：孙坚是已明身份的反贼、座位 1 是公开的主公 ⇒ 目标恒为座位 1（不登记
      ``_HOSTILE_SEAT_STEPS`` 会落进"帮盟友"分支，去指身份未知的座位 2）；
    - **选项步**：选【2】而不是【1】——判据取**可观测的后果**：X = 3 时【2】只摸 1 张、
      然后弃 3 张；【1】会摸 3 张、只弃 1 张。

    判据全走真引擎的记录流（摸牌 / 弃置各几条、各几张），不读打分函数内部。
    """
    hand = tuple(cards_of_type(PACK, "slash")[1:5])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=1, hp_max=4, identity=Identity.REBEL, hero=SUNJIAN_MOBILE),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LORD, hand=hand),
            SeatSpec(hp=4, hand=tuple(cards_of_type(PACK, "dodge")[1:3])),
        ],
        deck=deck_excluding(PACK, *hand, *cards_of_type(PACK, "dodge")[1:3]),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1)

    answers: dict[str, object] = {}
    for _ in range(2_000):
        if ran_timing(engine, Timing.PHASE_DRAW):
            break
        query = engine.pending
        if query is None:
            engine._step()
            continue
        kind = str(getattr(query.kind, "value", query.kind))
        skill = str(getattr(query, "skill_id", "") or "")
        if kind in ("SKILL_ACTIVATION", "SELECT_OPTION", "SELECT_CARDS"):
            action = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
            if kind == "SKILL_ACTIVATION" and skill == YINGHUN:
                answers["activate"] = action
            elif kind == "SELECT_OPTION" and skill.startswith("yinghun:"):
                answers[skill] = action
            engine.submit(action)
            continue
        engine.submit(PassPlayer().respond(query))

    activation = answers.get("activate")
    assert isinstance(activation, ActivateSkill) and activation.skill_id == YINGHUN, (
        f"AI 该发动【英魂】（没有代价，恒为正），实得 {activation!r}"
    )
    seat = answers.get("yinghun:seat")
    assert isinstance(seat, SelectOption) and seat.choice == "seat:1", (
        f"座位步该挑已明的敌人（主公 / 座位 1），实得 {seat!r}"
    )
    option = answers.get("yinghun:option")
    assert isinstance(option, SelectOption) and option.choice == YINGHUN_DRAW_ONE_DISCARD_X, (
        f"选项步该选【2】（摸 1 张再弃 X 张 = 拆牌），实得 {option!r}"
    )

    def _moves(seat: int, reason: MoveReason) -> list:
        return [
            record
            for record in engine.records
            if record.kind == "delta"
            and record.delta_type == "card_move"
            and record.reason is reason
            and record.owner == seat
        ]

    assert [len(record.cards) for record in _moves(1, MoveReason.DRAW)] == [1], (
        "【2】只摸一张（【1】会摸 X = 3 张）"
    )
    assert [len(record.cards) for record in _moves(1, MoveReason.DISCARD)] == [3], (
        "【2】弃 X = 3 张（【1】只弃 1 张）"
    )
    assert not _moves(2, MoveReason.DRAW) and not _moves(2, MoveReason.DISCARD), (
        "AI 不该把英魂指向身份未知的座位 2"
    )


# ---------------------------------------------------------------------------
# 林包·移动版·徐晃（lin-batch 票 03）：【断粮】在 160 档发动、在 108 档不去试
# ---------------------------------------------------------------------------

XUHUANG_MOBILE = "xuhuang_mobile"
DUANLIANG = "hero.xuhuang_mobile.duanliang"

PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 档：**【兵粮寸断】**只在这一档里（默认 108 档一张都没有 ⇒ Q2 = A 整条不亮）。"""

DUANLIANG_FILLERS = (2, 3)
"""座位 0 摸牌阶段摸到的两张（红【闪】：出牌阶段用不出去 ⇒ 不给 AI 竞品候选）。"""


def _find_judge_card(pack, exclude: tuple[int, ...] = ()) -> int:
    """一张非梅花的判定牌（花色与断粮的胜负无关；避开已用牌，免得牌堆里出现同一个 id）。"""
    used = {*DUANLIANG_FILLERS, *exclude}
    for card_id, card in enumerate(pack.deck.cards):
        if card_id in used:
            continue
        if card.suit != "club":
            return card_id
    raise AssertionError("牌堆里找不到非梅花牌")


def _duanliang_engine(*, pack, equip: tuple[int, ...]) -> Engine:
    """座位 0 = 徐晃移动版（``equip`` 里一张黑色非锦囊牌）+ 座位 1；跑到座位 0 的出牌阶段。

    源牌放**装备区**而不是手牌：手牌的黑色装备牌会先被 AI 装上去（装备分高于断粮的常量值），
    那样这条用例测的就不是"会不会发动断粮"了。装备区的牌在出牌阶段**没有**按原用途用出去
    这一条竞品候选 ⇒ 候选面只剩断粮与结束阶段，判据是确定性的。
    牌堆前两张固定是 :data:`DUANLIANG_FILLERS`，故摸牌阶段的收益与局势无关、逐种子可复现。
    """
    judge = _find_judge_card(pack, exclude=equip)
    rest = tuple(
        card_id
        for card_id in range(len(pack.deck.cards))
        if card_id not in {*equip, *DUANLIANG_FILLERS, judge}
    )
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hero=XUHUANG_MOBILE, equip=tuple(equip)), SeatSpec()],
        deck=(*DUANLIANG_FILLERS, judge, *rest),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: e.pending is not None
        and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE"
        and e.pending.actor == 0,
    )
    query = engine.pending
    assert query is not None and str(getattr(query.kind, "value", query.kind)) == "PLAY_PHASE"
    assert query.actor == 0
    return engine


def test_xuhuang_duanliang_ai_activates_on_the_160_deck() -> None:
    """160 档：【断粮】的清单在，AI **发动**它（复合动作 = 来源 + 目标），牌进目标判定区。"""
    black_equip = next(
        index
        for index, card in enumerate(PACK_160.deck.cards)
        if card.type == "qinggang_sword"
    )
    engine = _duanliang_engine(pack=PACK_160, equip=(black_equip,))
    query = engine.pending

    assert DUANLIANG in (query.transform_preactivations or {}), "160 档该有断粮的预发动清单"

    action = HeuristicPlayer(ai_stream(0, 0)).respond(query)

    assert isinstance(action, ActivateSkill) and action.skill_id == DUANLIANG, (
        f"AI 该发动【断粮】（无代价、恒为正），实得 {action!r}"
    )
    assert action.picks and action.choices, "复合动作要同时填来源与目标"

    engine.submit(action)
    # 使用要结算完才置入判定区：先把引擎推到"那张虚拟【兵粮寸断】已经落进判定区"。
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: any(
            record.kind == "delta"
            and record.delta_type == "card_move"
            and record.reason is MoveReason.PUT
            and record.to_zone.zone is Zone.JUDGE
            for record in e.records
        ),
    )

    put = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.PUT
    ]
    assert [(record.to_zone.seat, record.owner) for record in put] == [(1, 0)], (
        "转出来的【兵粮寸断】置入座位 1 的判定区（归属 = 使用者）"
    )
    assert engine.state.virtual_judge, "判定区那张实体牌代表一张**虚拟**牌"
    assert black_equip not in engine.state.player(0).equip, "源牌（装备区那张）已经用出去了"


def test_xuhuang_duanliang_ai_does_not_even_try_on_the_108_deck() -> None:
    """108 档：产物牌名不在牌堆 ⇒ 清单里连这条技能都没有（AI 没有可填的东西）。"""
    black_equip = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == "qinggang_sword"
    )
    engine = _duanliang_engine(pack=PACK, equip=(black_equip,))
    query = engine.pending

    assert DUANLIANG not in (query.transform_preactivations or {}), (
        "标准 108 档没有【兵粮寸断】实体牌 ⇒ 断粮不产出候选（账本 L938 口径）"
    )

    action = HeuristicPlayer(ai_stream(0, 0)).respond(query)

    assert not (isinstance(action, ActivateSkill) and action.skill_id == DUANLIANG), (
        f"AI 不该在 108 档试这条技能，实得 {action!r}"
    )


# ---------------------------------------------------------------------------
# 林包·移动版·曹丕（lin-batch2 票 01）：【放逐】把翻面丢给敌人（Q7 = A 的证据）
# ---------------------------------------------------------------------------

CAOPI_MOBILE = "caopi_mobile"
FANGZHU = "hero.caopi_mobile.fangzhu"


def _caopi_fangzhu_choice(*, monkeypatch_hostile: bool = False) -> tuple:
    """曹丕（已明的反贼）挨 1 点伤害 ⇒ 让**真实** AI 答「是否发动」与座位步。

    座位 1 / 3 = 身份未知，座位 2 = 公开的主公（反贼的敌人）。返回
    ``(activation, seat_choice, engine)`` —— 两者都是 ``HeuristicPlayer.respond`` 的产物。
    用一个四座身份局，使"未知座位"与"已明敌人"同时在场，敌我估值才有区分度。
    """
    if monkeypatch_hostile:
        # 合成反例：把 `fangzhu:` 从 `_HOSTILE_SEAT_STEPS` 里摘掉 ⇒ 落回"帮盟友"的缺省。
        monkeypatch_hostile_registration = tuple(
            step
            for step in heuristic_scoring._HOSTILE_SEAT_STEPS
            if step != "fangzhu:"
        )
        heuristic_scoring._HOSTILE_SEAT_STEPS = monkeypatch_hostile_registration
    channel = cards_of_type(PACK, "slash")[5]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=CAOPI_MOBILE, hp=3, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hand=tuple(cards_of_type(PACK, "dodge")[1:3])),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LORD),
            SeatSpec(hp=4, hand=tuple(cards_of_type(PACK, "peach")[1:3])),
        ],
        deck=deck_excluding(
            PACK,
            channel,
            *cards_of_type(PACK, "dodge")[1:3],
            *cards_of_type(PACK, "peach")[1:3],
        ),
        processing=(channel,),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    engine = Engine.from_state(state, seed=1)
    channel_ref = state.card(channel)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 1,
            "targets": (0,),
            "card": channel_ref,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 0,
            "channel": channel_ref,
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )

    activation = None
    seat_choice = None
    for _ in range(2_000):
        if ran_timing(engine, Timing.DAMAGE_END) or engine.finished:
            break
        query = engine.pending
        if query is None:
            engine._step()
            continue
        if query.kind == "SKILL_ACTIVATION" and str(query.skill_id) == FANGZHU:
            activation = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
            engine.submit(activation)
            continue
        if query.kind == "SELECT_OPTION" and str(query.skill_id) == "fangzhu:seat":
            seat_choice = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
            engine.submit(seat_choice)
            continue
        engine.submit(PassPlayer().respond(query))
    return activation, seat_choice, engine


def test_caopi_fangzhu_ai_aims_at_the_enemy() -> None:
    """【放逐】：发动后座位步指向**已明的敌人**（`_HOSTILE_SEAT_STEPS` 里 `fangzhu:` 的证据）。

    票面 §AI 适配 第 2 / 6 格：座位步的敌我估值读 ``skill_id``（``_score_seat_option`` 查
    ``_HOSTILE_SEAT_STEPS``）⇒ 不登记就静默落进"帮盟友"分支（会把队友翻面、跳过其整个
    下个回合，纯自伤）。判据全走真引擎：AI 在「是否发动」上答 ``ActivateSkill``、在座位步
    上选**主公（座位 2）**，且终局状态的 ``flipped_change`` 落在座位 2。
    """
    activation, seat_choice, engine = _caopi_fangzhu_choice()

    assert isinstance(activation, ActivateSkill) and activation.skill_id == FANGZHU, (
        f"AI 该发动【放逐】（没有代价），实得 {activation!r}"
    )
    assert isinstance(seat_choice, SelectOption), f"座位步要给出选择，实得 {seat_choice!r}"
    assert seat_choice.choice == "seat:2", (
        f"AI 该把翻面丢给已明的敌人（主公 / 座位 2），实得 {seat_choice.choice!r}"
    )
    assert engine.state.player(2).flipped is True, "敌人被翻面"
    assert engine.state.player(1).flipped is False, "身份未知的座位 1 不动"


def test_caopi_fangzhu_seat_value_comes_from_the_hostile_registration() -> None:
    """**合成反例**：摘掉 `fangzhu:` 登记 ⇒ AI 不再挑敌人（落回"帮盟友"缺省）。

    与 `tests/test_heuristic_ai.py` 的"删一条 ⇒ 正向检查变红"同一条纪律：把登记摘掉必须
    观察到行为翻转，否则那条登记只是装饰。缺省分支下已明敌人是负分、未知座位是 0 分
    ⇒ 座位步改指身份未知的座位。
    """
    saved = heuristic_scoring._HOSTILE_SEAT_STEPS
    try:
        activation, seat_choice, engine = _caopi_fangzhu_choice(
            monkeypatch_hostile=True
        )
    finally:
        heuristic_scoring._HOSTILE_SEAT_STEPS = saved

    assert isinstance(activation, ActivateSkill), "是否发动不受影响"
    assert isinstance(seat_choice, SelectOption)
    assert seat_choice.choice != "seat:2", (
        f"没有登记就不再挑敌人（缺省 = 帮盟友），实得 {seat_choice.choice!r}"
    )
    assert engine.state.player(2).flipped is False, "敌人不再被翻面"


def test_the_jie_jianying_target_ask_is_registered_as_a_hostile_seat_step() -> None:
    """【渐营】② 的【杀】目标询问 **真的** 命中 `_HOSTILE_SEAT_STEPS`（反向对齐登记表）。

    票 18 把"产物打谁"从**提交点后手写的一问**搬进流水线第 3 步，询问 id 因此从
    ``hero.jie_jushou_mobile.jianying.target`` 变成 ``<短技能 id>:<步骤键>``。登记串按
    ``startswith`` 用 ⇒ 少一个冒号 / 沿用全限定写法就永不命中、② 的【杀】静默落进
    "帮盟友"分支，而"表里有这一条"的断言不会红。

    询问 id **不在这里重拼**（`CODING_STANDARDS.md` §6）：它由真实产出方的两个函数给出 ——
    SKILL 帧载荷里的 ``skill_id``（:meth:`~sgs.engine.Engine._skill_payload` 写它，本测试
    真的调它；被动帧走 ``running_skill`` 那一档，`flow._flow_option_id` 同样认）与
    :func:`~sgs.skills.flow._flow_option_id`（派生出短 id 与冒号）。两档都问：
    同一步问多次时 id 补序号（``seat:0``），登记前缀必须两档都命中。
    """
    from types import SimpleNamespace

    from sgs.skills.active import JIE_JIANYING, JIE_JIANYING_FLOW
    from sgs.skills.flow import _flow_option_id

    seat = next(step for step in JIE_JIANYING_FLOW.steps if step.key == "seat")
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK),
        rule_config=RuleConfig(player_count=2),
    )
    data = Engine.from_state(state, seed=1)._skill_payload(  # noqa: SLF001 - 真实写方
        ActivateSkill(skill_id=JIE_JIANYING, answers_seq=0), 0
    )
    frame = SimpleNamespace(data=data)
    ask_ids = (
        _flow_option_id(frame, seat.key, 0, multi=False),
        _flow_option_id(frame, seat.key, 0, multi=True),
    )

    assert all(
        ask_id.startswith(heuristic_scoring._HOSTILE_SEAT_STEPS)
        for ask_id in ask_ids
    ), f"询问 id {ask_ids!r} 不匹配任何登记前缀 ⇒ 座位步按'帮盟友'给分"


# ---------------------------------------------------------------------------
# 林包·移动版·董卓（lin-batch2 票 02）：【崩坏】的静态二选一（Q7 = A 的证据）
# ---------------------------------------------------------------------------

DONGZHUO_MOBILE_ID = "dongzhuo_mobile"
BENGKUAI_LOSE_HP = "bengkuai_lose_hp"
BENGKUAI_REDUCE_MAX = "bengkuai_reduce_max"


def _bengkuai_option_choice() -> tuple:
    """董卓（群 / 8 体力上限）自己的结束阶段：有人体力严格更低 ⇒ 真的 AI 答选项步。

    判据走**真询问**：``SELECT_OPTION`` + ``skill_id == "bengkuai:option"`` 的那一问
    （锁定技不问「是否发动」，所以这是本技能唯一的面）。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=DONGZHUO_MOBILE_ID, hp=3, hp_max=8),
            SeatSpec(hp=4),
            SeatSpec(hp=2),
        ],
        deck=deck_excluding(PACK),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=1)
    choice = None
    for _ in range(20_000):
        query = engine.pending
        if query is None:
            if ran_timing(engine, Timing.TURN_END) or engine.finished:
                break
            engine._step()
            continue
        if query.kind == "SELECT_OPTION" and str(getattr(query, "skill_id", "")) == (
            "bengkuai:option"
        ):
            choice = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
            engine.submit(choice)
            break
        engine.submit(PassPlayer().respond(query))
    return choice, engine


def test_bengkuai_ai_prefers_losing_hp_over_reducing_max_hp() -> None:
    """【崩坏】两格都是成本：AI 宁失 1 点体力（可回复）也不缩上限（永久）。

    票面 `## AI 适配` Q7 = A：`_OPTION_VALUES` 的 `bengkuai_lose_hp = -1.2` /
    `bengkuai_reduce_max = -1.6`。**已知限制**：1 体力时「失体力」= 进濒死，静态常量
    读不出这个（要按当前体力细算得另开 AI 票），本用例把现状钉住、不假装它是对的。
    """
    choice, _engine = _bengkuai_option_choice()

    assert isinstance(choice, SelectOption), f"选项步要给出选择，实得 {choice!r}"
    assert choice.choice == BENGKUAI_LOSE_HP, (
        f"AI 该选「失去1点体力」（-1.2 > -1.6），实得 {choice.choice!r}"
    )


# ---------------------------------------------------------------------------
# 林包·移动版·贾诩（lin-batch2 票 03）：【乱武】的"敌人对最近者出【杀】"
# ---------------------------------------------------------------------------

JIAXU_MOBILE_ID = "jiaxu_mobile"
LUANWU_ID = "hero.jiaxu_mobile.luanwu"


def _luanwu_ai_case() -> tuple[dict, Engine]:
    """四座身份局：贾诩（主公）发【乱武】，座位 1（明置反贼）对最近者出【杀】。

    贾诩**空手** ⇒ 出牌阶段唯一有正分的动作就是【乱武】（`SKILL_VALUES` 2.5）；
    座位 1 的最近者是 0（贾诩，主公 = 明置敌人）与 2（明置忠臣 = 盟友）⇒
    **并列最近**那一问按 `_HOSTILE_SEAT_STEPS` 挑敌人、下一问按 Q9 = B 给敌人正分。
    """
    pack = PACK
    slash = cards_of_type(pack, "slash")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero=JIAXU_MOBILE_ID, hp=3, hp_max=3, identity=Identity.LORD),
            SeatSpec(hand=(slash,), identity=Identity.REBEL),
            SeatSpec(identity=Identity.LOYALIST),
            SeatSpec(identity=Identity.REBEL),
        ],
        # 牌堆**头两张是【闪】**：贾诩的摸牌阶段只会摸到不可用的牌 ⇒ 出牌阶段唯一有正分的
        # 动作就是【乱武】（否则他会先把手里的锦囊用掉，用例测的就不是乱武了）。
        deck=tuple(cards_of_type(pack, "dodge"))
        + tuple(
            index
            for index in range(len(pack.deck.cards))
            if index != slash and pack.deck.cards[index].type != "dodge"
        ),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4),
    )
    state.player(0).identity_visible = True
    state.player(1).identity_visible = True
    state.player(2).identity_visible = True

    engine = Engine.from_state(state, seed=5)
    found: dict = {
        "activation": None,
        "target": None,
        "response": None,
        "hp_before": state.player(0).hp,
    }
    for _ in range(20_000):
        query = engine.pending
        if query is None:
            if ran_timing(engine, Timing.TURN_END) or engine.finished:
                break
            engine._step()
            continue
        if query.kind == "PLAY_PHASE" and query.actor == 0 and found["activation"] is None:
            choice = HeuristicPlayer(ai_stream(0, 0)).respond(query)
            found["activation"] = choice
            engine.submit(choice)
            continue
        if query.kind == "SELECT_OPTION" and str(
            getattr(query, "skill_id", "")
        ).endswith(":target"):
            choice = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
            found["target"] = choice
            engine.submit(choice)
            continue
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LUANWU
            and found["response"] is None
        ):
            choice = HeuristicPlayer(ai_stream(0, query.actor)).respond(query)
            found["response"] = choice
            engine.submit(choice)
            continue
        engine.submit(PassPlayer().respond(query))
    found["hp_after"] = engine.state.player(0).hp
    return found, engine


def test_jiaxu_ai_activates_luanwu_and_an_enemy_slashes_the_nearest() -> None:
    """【乱武】的整条 AI 链路：贾诩发动 → 敌人挑敌人为目标 → 那一问真的出【杀】。

    三处判据各自对应票面 `## AI 适配` 的三格：① `SKILL_VALUES[乱武]` 正分 ⇒ 发动；
    ② 并列最近的目标步登记在 `_HOSTILE_SEAT_STEPS` ⇒ 选敌人；③ Q9 = B ⇒ 对敌人出
    【杀】（而不是"能出就出"地打盟友、也不是白失 1 点体力）。
    """
    found, _engine = _luanwu_ai_case()

    assert isinstance(found["activation"], ActivateSkill), f"该发动【乱武】，实得 {found['activation']!r}"
    assert found["activation"].skill_id == LUANWU_ID
    assert isinstance(found["target"], SelectOption) and found["target"].choice == "seat:0", (
        f"并列最近的最近者里该挑明置敌人（贾诩），实得 {found['target']!r}"
    )
    assert isinstance(found["response"], UseCard), f"该出【杀】，实得 {found['response']!r}"
    assert found["response"].targets == (0,)
    assert found["hp_after"] == found["hp_before"] - 1, "那张【杀】真的打中了贾诩"


# ---------------------------------------------------------------------------
# 一将成名-2014·顾雍（yijiang-2014 票 03）：【慎行】弃两张恒摸一张
# ---------------------------------------------------------------------------

GUYONG_STD = "guyong"
GUYONG_SHENXING = "hero.guyong.shenxing"


def test_guyong_shenxing_ai_fills_the_manifest_and_filters_two_cards_into_one() -> None:
    """【慎行】：AI 会填预发动清单（两张出牌阶段用不出去的牌）并完成"弃二摸一"。

    手里只放【闪】+【无懈可击】：两者在出牌阶段都**用不出去**，故"发动【慎行】"是唯一
    有意义的动作（否则 AI 会先按原用途把牌用掉）。本版**没有次数上限**、恒摸一张，
    `SKILL_VALUES` 给的是低正分 ⇒ 判据只有"会发动 + 把两张都填进清单"。
    """
    dodge = cards_of_type(PACK, "dodge")[0]
    nullify = cards_of_type(PACK, "nullify")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=GUYONG_STD, hand=(dodge, nullify)), SeatSpec()],
        deck=deck_excluding(PACK, dodge, nullify),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    # 摸牌阶段放弃摸牌（`set_draw_phase_count(0, 0)`）：出牌阶段手里就只有可付的那两张。
    engine.set_draw_phase_count(0, 0)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: running.pending is not None
        and running.pending.kind == "PLAY_PHASE",
    )
    play = engine.pending
    action = HeuristicPlayer(ai_stream(0, play.actor)).respond(play)

    assert isinstance(action, ActivateSkill) and action.skill_id == GUYONG_SHENXING, (
        f"AI 该发动【慎行】，实得 {action!r}"
    )
    assert set(action.picks) == {dodge, nullify} and len(action.picks) == 2, (
        f"清单要恰好填那两张，实得 {action.picks!r}"
    )
    engine.submit(action)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda running: dodge in running.state.discard and nullify in running.state.discard,
    )
    assert dodge in engine.state.discard and nullify in engine.state.discard
    assert len(engine.state.player(0).hand) == 1, "弃两张、摸一张"
