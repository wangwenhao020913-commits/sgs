"""**需求传递**（need-card-supply 票 03）：【护驾】/【激将】的供给 = relay。

用户 2026-11-20 Q2（逐字，`spec.md` §6）：

    当曹操成为锋·关羽杀的目标且只能使用方块牌响应时，可以发动护驾（护驾的发动无须交验
    合法性，因为不是直接提供转化/虚拟牌，而是全场逐一询问魏势力角色，护驾真正做的是把自己
    '需求方块闪'这个需求依次传递，直到有个其它角色能够'提供'这个'需求'时，这个角色就可以
    使用/打出符合'需求'的闪，视为曹操使用/打出这张牌）但是假设曹操被'义绝不能出牌'或被
    '杨仪共损不能出闪'……则曹操的【护驾】的 supply 直接为 none，就不会再依次逐座位传递'需求'了

判据（票面 §3 的 1–4 / 6）：

1. **relay 供给已知**（§3-1 / S9 前半）：花色需求生效时【护驾】**进面**——票 02 之前它被
   "限制生效 ⇒ 提供者失败关闭"挡住，故这条断言就是"多禁被放开"的证明
   （``test_a_suit_demand_lets_the_relay_provider_into_the_face``）；
2. **持有者被挡 ⇒ 供给 none ⇒ 不开链**（§3-2 / S9）：被【义绝】封住手牌时①【护驾】不进候选、
   ②求助窗口**一次都不问**别的魏势力座位——数**询问次数**，不是数候选
   （``test_a_blocked_holder_offers_no_relay_and_never_opens_the_chain``）；
3. **需求传递**（§3-3 / S10 / 票面 §2b-2）：同一局面下盟友那一问的 ``Demand`` 与持有者那一
   问**逐字段相同**（花色白名单同一个取值），盟友面里花色不符的【闪】**不进候选**
   （``test_the_ally_question_carries_the_holders_demand_unchanged``）；
4. **上游禁令复用**（§3-4 / S11）：【定乱】的牌不能当【杀】打出（``forbidden_as_slash``）——
   这条既判据在求助链里照样成立，不另写白名单
   （``test_the_relay_chain_reuses_the_upstream_prohibition``）。

**本票不做的**（票 04 的活）：【蛊惑】的预转化、逐张手牌的供给（票 06 起只剩供给模型）、
持有者独享的载荷。
**本票也不碰**子窗口的受理面（``_run_lord_request`` 仍只收 ``PlayCard``，见 ``## Answer``）。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
    if str(_entry) not in sys.path:  # 与 ``test_web.py`` 同一条引导（``sgs`` + ``sgs_web``）
        sys.path.insert(0, str(_entry))

from sgs.data import (  # noqa: E402
    CardTagType,
    Identity,
    Kingdom,
    load_content_pack,
)
from sgs.effects import (  # noqa: E402
    Demand,
    Supply,
    demand_for,
    legal_actions_for,
    supply_is_legal,
    supply_satisfies,
)
from sgs.engine import Engine  # noqa: E402
from sgs.skills import CATALOG  # noqa: E402
from sgs.state import (  # noqa: E402
    ActivateSkill,
    CardTag,
    DodgeContext,
    Pass,
    PlayCard,
    QueryKind,
    RESPONSE_SUITS_KEY,
    RespondContext,
    RespondMode,
    WindowCause,
)

from support import (  # noqa: E402
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")
"""标准 108 张档（模块级加载必须写死档位，票 32）。"""

HUJIA = "hero.caocao.hujia"
JIJIANG = "hero.liubei.jijiang"
YIJUE = "hero.jie_guanyu.yijue"
"""界关羽【义绝】的技能 id：写进 ``hand_card_prohibition_sources`` 的那个"施加者"。"""

DINGLUAN = "hero.feng_yujin.dingluan"
"""锋·于禁【定乱】的技能 id（标签来源；读点 = ``effects.actions.forbidden_as_slash``）。"""

LORD_SEAT = 0
ATTACKER = 1
ALLY = 2
"""三个座位的固定角色：需要者（主公）/ 攻击者（非同盟，不会被求助）/ 同势力盟友。"""


# ---------------------------------------------------------------------------
# 局面构造
# ---------------------------------------------------------------------------


def _suit_card(type_id: str, suit: str) -> int:
    """标准档里某牌名某花色的第一张 ``card_id``。"""
    return next(
        card_id
        for card_id in cards_of_type(PACK, type_id)
        if PACK.deck.cards[card_id].suit == suit
    )


DIAMOND_DODGE = _suit_card("dodge", "diamond")
HEART_DODGE = _suit_card("dodge", "heart")
HEART_SLASH = _suit_card("slash", "heart")
SLASHES = tuple(cards_of_type(PACK, "slash"))
SLASH, SLASH_TWO = SLASHES[0], SLASHES[1]
"""两张不同的【杀】：一张挂【定乱】、一张不挂，才分得出"是判据挡的"还是"本来就没有"。"""
DUEL = cards_of_type(PACK, "duel")[0]


def _prohibit(engine: Engine, seat: int) -> None:
    """给座位加一条"本回合不能使用或打出手牌"（唯一写入口；施加者 = 【义绝】）。"""
    assert engine.add_hand_card_prohibition(seat, YIJUE) is True


def _hujia_state(*, blocked: bool, hand: tuple[int, ...] = ()):
    """曹操（座位 0，魏，主公）+ 一个**魏**势力盟友 + 一个蜀势力攻击者。

    ``turn_seat = ATTACKER`` ⇒ ``_request_askers`` 的环是 ``(1, 2, 0)``：座位 1 不同势力
    （不问）、座位 2 同势力（**唯一**会被求助窗口问到的座位）。盟友手里 ♦ / ♥ 各一张【闪】——
    花色需求 "♦" 生效时，♥ 那张不能进盟友的面（§3-3 后半）。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hand=hand, hero="caocao", identity=Identity.LORD, kingdom=Kingdom.WEI
            ),
            SeatSpec(hand=(SLASH_TWO,), kingdom=Kingdom.SHU),
            SeatSpec(hand=(DIAMOND_DODGE, HEART_DODGE), kingdom=Kingdom.WEI),
        ],
        deck=deck_excluding(PACK, SLASH_TWO, DIAMOND_DODGE, HEART_DODGE, *hand),
        turn_seat=ATTACKER,
    )
    if blocked:
        _prohibit(Engine.from_state(state, seed=3), LORD_SEAT)
    return state


def _jijiang_state(
    *, blocked: bool, ally_hand: tuple[int, ...], tag: int | None = None, hand: tuple[int, ...] = ()
):
    """刘备（座位 0，蜀，主公）+ 一个**蜀**势力盟友 + 一个魏势力攻击者。

    窗口 = 【决斗】（``required = "slash"``，目标是刘备 ⇒ 刘备先被问）：他便可以发动
    【激将】，把"打出一张【杀】"这个需求传给座位 2。``tag`` = 要挂【定乱】的那张牌。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hand=hand, hero="liubei", identity=Identity.LORD, kingdom=Kingdom.SHU
            ),
            SeatSpec(hand=(DUEL,), kingdom=Kingdom.WEI),
            SeatSpec(hand=ally_hand, kingdom=Kingdom.SHU),
        ],
        deck=deck_excluding(PACK, DUEL, *hand, *ally_hand),
        turn_seat=ATTACKER,
    )
    if tag is not None:
        state.card_tags[tag] = (
            CardTag(tag_type=CardTagType.DINGLUAN, owner=ATTACKER, skill_id=DINGLUAN),
        )
    if blocked:
        _prohibit(Engine.from_state(state, seed=3), LORD_SEAT)
    return state


class _Relay:
    """脚本参与者：主公**能发就发**，盟友**能打就打**；其余按最小合法应答（``PassPlayer``）。"""

    def __init__(self, skill_id: str) -> None:
        self._skill_id = skill_id

    def respond(self, query):
        for action in query.legal_actions:
            if isinstance(action, ActivateSkill) and action.skill_id == self._skill_id:
                return action
        for action in query.legal_actions:
            if isinstance(action, PlayCard):
                return action
        return PassPlayer().respond(query)


def _resolved(engine: Engine, players) -> Engine:
    """把这一次使用推到它的帧出栈（求助窗口可能跨多次重入）。"""
    return run_queries(engine, players, lambda e: not e.stack.frames)


def _stopped_at_ally_question(engine: Engine, players) -> Engine:
    """推到**盟友那一问**挂起为止（此时承载帧还在栈上，需求可以从帧上读）。"""
    return run_queries(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == "RESPOND"
        and e.pending.actor == ALLY,
    )


def _queries_of_kind(engine: Engine, kind: str):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _asked(engine: Engine, seat: int = ALLY):
    """引擎**真的问过**这个座位的全部询问（数询问次数用这一份，不数候选）。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and int(record.query.actor) == seat
    ]


def _lord_request_queries(engine: Engine):
    return [
        query
        for query in _queries_of_kind(engine, "RESPOND")
        if getattr(query, "cause", None) is WindowCause.LORD_REQUEST
    ]


def _window_frame(engine: Engine, query):
    """这一问的**承载帧**（``RespondQuery.window_frame`` 指名的那一帧；缺省取栈顶）。"""
    frames = tuple(engine.stack.frames)
    window_frame = int(getattr(query, "window_frame", 0) or 0)
    if window_frame:
        for frame in reversed(frames):
            if int(getattr(frame, "frame_id", -1)) == window_frame:
                return frame
    return frames[-1] if frames else None


def _providers(actions) -> list[str]:
    return [action.skill_id for action in actions if isinstance(action, ActivateSkill)]


# ---------------------------------------------------------------------------
# 1. relay 是**可判**的供给（票面 §1 的"匹配"那一条）
# ---------------------------------------------------------------------------


def test_a_relay_supply_is_concrete_and_forwarded_not_judged_dead():
    """relay = 具体供给（``unknown=False``）+ 牌名之后**不判死** + 合法性与匹配器同源。

    三条一起读才是"需求传递"：①它不再是"候选期不可知"；②花色需求下**不判死**（需求要传给
    同势力角色，答案在那一问里）；③合法性闸门因此不再失败关闭它——这正是票 02 的
    ``unknown`` 分支"放开"的落点（票面 §2b-1：不在闸门里加技能名白名单）。
    """
    relay = CATALOG[HUJIA].supply
    assert relay is not None
    assert (relay.kind, relay.unknown) == ("relay", False)

    demand = Demand(names=("dodge",), suits=("diamond",))
    assert supply_satisfies(demand, relay) is True, "花色需求下 relay 不判死（需求传递）"
    assert supply_is_legal(demand, relay) is True, "可判 ⇒ 闸门与匹配器同源"

    # 牌名维度**仍然判**（它是声明事实）：relay 不是"哪一问都能进"。
    assert supply_satisfies(Demand(names=("slash",)), relay) is False


def test_the_relay_branch_is_scoped_to_relay_and_leaves_the_other_kinds_alone():
    """同一条收窄需求下：八卦阵（已知、无花色）**仍**不满足；预转化（unknown）**仍**失败关闭。

    反例断言的作用：证明上面那条 ``True`` 是 relay 一族的判据，而不是"需求被放宽了"。
    """
    demand = Demand(names=("dodge",), suits=("diamond",))

    bagua = Supply(product="dodge", kind="virtual")
    assert supply_satisfies(demand, bagua) is False, "无花色**确定地**不满足花色需求"
    assert supply_is_legal(demand, bagua) is False

    guohuo = Supply(product="dodge", kind="preconversion", unknown=True)
    assert supply_satisfies(demand, guohuo) is True, "unknown 不判死（票 01 的展示口径）"
    assert supply_is_legal(demand, guohuo) is False, "unknown + 收窄需求 ⇒ 仍失败关闭（票 04）"


def test_a_suit_demand_lets_the_relay_provider_into_the_face():
    """§3-1 / S9 前半：花色需求生效时曹操的【护驾】**进面**（用户 Q2：发动不验合法性）。

    "进面"= ``legal_actions_for`` 交到玩家手上的那一面里有那条 ``ActivateSkill``。票 02 的
    失败关闭在同一个局面上会给空（它按 ``unknown`` 一刀切），故这条断言就是"多禁被放开"。
    """
    state = _hujia_state(blocked=False)
    payload = DodgeContext(response_suits=("diamond",))

    assert _providers(
        legal_actions_for(state, LORD_SEAT, QueryKind.DODGE, answers_seq=0, payload=payload)
    ) == [HUJIA]

    # 不限花色时同样在（票 01 之前的行为逐条不变）。
    assert _providers(
        legal_actions_for(
            state, LORD_SEAT, QueryKind.DODGE, answers_seq=0, payload=DodgeContext()
        )
    ) == [HUJIA]


# ---------------------------------------------------------------------------
# 2. 持有者被挡 ⇒ 供给 none ⇒ 不开询问链（§3-2 / S9）
# ---------------------------------------------------------------------------


def _run_hujia_chain(*, blocked: bool) -> Engine:
    """一次真使用链：座位 1 用【杀】打曹操，花色需求 = ♦（单骑第三段的形状）。"""
    state = _hujia_state(blocked=blocked)
    engine = Engine.from_state(state, seed=11)
    engine.start_use(
        state.card(SLASH_TWO),
        source=ATTACKER,
        targets=(LORD_SEAT,),
        response_suits=("diamond",),
    )
    players = [_Relay(HUJIA), PassPlayer(), _Relay(HUJIA)]
    return _resolved(engine, players)


def test_a_blocked_holder_offers_no_relay_and_never_opens_the_chain():
    """§3-2 / S9：被【义绝】封住手牌 ⇒ ①【护驾】不进候选；②求助窗口**一次都不问**。

    ②数的**询问次数**（引擎记录流里的 query 条数），不是候选数：链开没开只有这一种读法。
    两条一起成立才是"供给 none"，只看候选的话"候选少了"也可能是别的过滤。
    """
    blocked = _run_hujia_chain(blocked=True)
    dodge = _queries_of_kind(blocked, "DODGE")
    assert dodge, "构造：曹操应当被问【闪】"
    assert _providers(dodge[0].legal_actions) == [], "① 被挡 ⇒【护驾】不进候选"
    assert [query for query in _asked(blocked) if query.kind == "RESPOND"] == [], (
        "② 链没开：盟友一次都没被问"
    )
    assert _lord_request_queries(blocked) == []
    assert blocked.state.player(LORD_SEAT).hp == 3, "没有【闪】⇒ 照常挨这一刀（不是被静默免掉）"

    # 反例（同一条链、只差禁令）：没被挡时它**在**候选里，且盟友**恰好被问一次**。
    free = _run_hujia_chain(blocked=False)
    assert _providers(_queries_of_kind(free, "DODGE")[0].legal_actions) == [HUJIA]
    assert len(_lord_request_queries(free)) == 1, "链开了：盟友被问一次（首个打出即止）"
    assert free.state.player(LORD_SEAT).hp == 4, "盟友打出的【闪】真的抵消了这张【杀】"


def test_a_suppressed_relay_skill_is_not_offered():
    """技能失效走 ``resolve_instances`` 的既有口径：连实例都不产出 ⇒ 无候选。

    这是票面 §1 里"技能失效 / 未生效"那一格的读法——它**不是**本票新造的判据，而是既有的
    解析口径自然给出的结论（"这一档还在、只是给不出来"的展示行已随票 05 / 06 删除，
    spec §0.1b）。反过来，若候选生成绕过实例解析去扫目录，这一档就会多出一条玩家点不到的
    候选。
    """
    state = _hujia_state(blocked=False)
    engine = Engine.from_state(state, seed=3)
    engine.suppress_skills(LORD_SEAT, YIJUE)

    payload = DodgeContext(response_suits=("diamond",))
    assert _providers(
        legal_actions_for(state, LORD_SEAT, QueryKind.DODGE, answers_seq=0, payload=payload)
    ) == [], "失效的【护驾】不产出候选"


# ---------------------------------------------------------------------------
# 3. 子窗口带同一份 Demand（§3-3 / S10；票面 §2b-2 的"先写一条断言证明它"）
# ---------------------------------------------------------------------------


def test_the_ally_question_carries_the_holders_demand_unchanged():
    """盟友那一问的 ``Demand`` 与持有者那一问**逐字段相同**；盟友面里花色不符的【闪】不进候选。

    票面 §2b-2：花色需求**已经**由承载帧的 ``RESPONSE_SUITS_KEY`` 传进子窗口（票 02 已核），
    本票只写断言证明它——不重实现。故这里同时断"不是空转"：白名单必须是 ``("diamond",)``。
    """
    state = _hujia_state(blocked=False)
    engine = Engine.from_state(state, seed=11)
    engine.start_use(
        state.card(SLASH_TWO),
        source=ATTACKER,
        targets=(LORD_SEAT,),
        response_suits=("diamond",),
    )
    players = [_Relay(HUJIA), PassPlayer(), _Relay(HUJIA)]
    engine = _stopped_at_ally_question(engine, players)

    ally_query = engine.pending
    assert ally_query is not None and ally_query.actor == ALLY
    assert getattr(ally_query, "cause", None) is WindowCause.LORD_REQUEST
    frame = _window_frame(engine, ally_query)
    assert frame is not None
    suits = frame.data.get(RESPONSE_SUITS_KEY)

    # 两边各走自己的**生产路径**：持有者那一问读【闪】窗口的载荷，盟友那一问读
    # ``respond_context`` 从承载帧继承的同一份事实（票面 §2b-2 说的那条线）。
    holder_demand = demand_for(
        QueryKind.DODGE, DodgeContext(response_suits=None if suits is None else tuple(suits))
    )
    ally_ctx = engine.respond_context(
        frame,
        ALLY,
        cause=WindowCause.LORD_REQUEST,
        required="dodge",
        target=LORD_SEAT,
        mode=RespondMode.PLAY,
    )
    ally_demand = demand_for(QueryKind.RESPOND, ally_ctx)

    assert holder_demand == ally_demand, "子窗口带的是**同一份**需求（逐字段相等）"
    assert ally_demand == Demand(names=("dodge",), suits=("diamond",)), (
        "不是空转：花色白名单真的到了盟友那一问"
    )

    # 盟友面里花色不符的【闪】**不进候选**（后端算，用户 Q4）：♦ 在、♥ 不在。
    played = [
        action.card.card_id
        for action in ally_query.legal_actions
        if isinstance(action, PlayCard)
    ]
    assert played == [DIAMOND_DODGE], (
        f"盟友面里只该有 ♦ 那张【闪】：{ally_query.legal_actions}"
    )


# ---------------------------------------------------------------------------
# 4. 上游禁令复用既有谓词（§3-4 / S11）
# ---------------------------------------------------------------------------


def _run_jijiang_chain(*, tag: int | None) -> Engine:
    """一次真使用链：座位 1 对刘备使用【决斗】⇒ 刘备发动【激将】⇒ 座位 2 被问【杀】。"""
    state = _jijiang_state(blocked=False, ally_hand=(SLASH, SLASH_TWO), tag=tag)
    engine = Engine.from_state(state, seed=13)
    engine.start_use(state.card(DUEL), source=ATTACKER, targets=(LORD_SEAT,))
    players = [_Relay(JIJIANG), PassPlayer(), _Relay(JIJIANG)]
    return _resolved(engine, players)


def _relay_playable(engine: Engine) -> list[int]:
    """求助子窗口里盟友**能打出**的那几张【杀】（真实询问的候选，不是另算一遍）。"""
    return [
        action.card.card_id
        for query in _lord_request_queries(engine)
        for action in query.legal_actions
        if isinstance(action, PlayCard)
    ]


def test_the_relay_chain_reuses_the_upstream_prohibition():
    """§3-4 / S11：【定乱】的牌不能当【杀】打出——求助链里的候选生成**读同一条既判据**。

    同一个局面只差一枚标签：两张【杀】都在盟友手里，带标签的那张在求助子窗口里**不存在**。
    判据住在 ``_required_cards``（``forbidden_as_slash``），本票没有、也不该另写一份白名单
    ——否则"盟友能替主公打出的牌"会与"他自己响应时能打出的牌"分成两套真源。
    """
    from sgs.effects import forbidden_as_slash

    free = _run_jijiang_chain(tag=None)
    assert sorted(_relay_playable(free)) == sorted((SLASH, SLASH_TWO)), (
        "对照：没有标签时两张【杀】都能替主公打出"
    )

    tagged = _run_jijiang_chain(tag=SLASH)
    assert forbidden_as_slash(tagged.state, tagged.state.card(SLASH)) is True
    assert _relay_playable(tagged) == [
        SLASH_TWO
    ], "【定乱】那张被既判据挡在求助链之外（不是被新白名单挡的）"
