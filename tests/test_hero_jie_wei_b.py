"""M6 票 15：界·魏 B（界郭嘉 / 界李典）。

四条技能，逐条走**外部缝**（记录流 / 询问缝 / 终局状态），与各族的既有测试同一姿态：

=====================  ==============================================================
技能                     牌面（§十九 L2040–L2044、L2050–L2057）与落点
=====================  ==============================================================
界郭嘉【天妒】           与标准【天妒】逐字相同（判定牌生效后获得之）
界郭嘉【遗计】           "受到 1 点伤害后，你可以摸两张牌，然后你可以交给至多两名
                        其他角色共计至多两张手牌"——与标准版的"观看牌堆顶两张再分配"
                        是**两种**效果：界版摸的是牌堆顶、分配的是**自己的手牌**
界李典【恂恂】           摸牌阶段开始时观看牌堆顶四张，两张置顶、其余置底
                        （固定切分 2 / 2，两张置顶的顺序可选）
界李典【忘隙】           "当你对其他角色造成 1 点伤害后，或受到其他角色的 1 点伤害后，
                        你可以摸两张牌并交给该角色其中一张牌"——**双向**、逐点触发
=====================  ==============================================================

**分配动作走既有的给牌路径**（票面验收）：【遗计】的"交给谁"与【忘隙】的"交给该角色"
都用既有的 ``SELECT_CARDS`` / ``SELECT_OPTION`` 询问 + ``move_in_frame(... PUT ...)``
（与标准【遗计】的给牌同一条），不新造私有 Action 类型。

**逐点触发**：【忘隙】的"造成 1 点伤害后"落在 ``DAMAGE_AFTER_DEAL`` 上。该时机原本每次
伤害事件只派发一轮；本票按票 05 在 ``DAMAGE_AFTER_TAKE`` 上的同一范式把它扩成逐点
（``docs/agents/domain.md`` 的"触发次数是技能定义的一部分"）。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    EndPhase,
    FrameKind,
    Pass,
    SelectCards,
    SelectOption,
)
from sgs.timing import Timing

from support import (
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")

JIE_TIANDU = "hero.jie_guojia.tiandu"
JIE_YIJI = "hero.jie_guojia.yiji"
XUNXUN = "hero.jie_lidian.xunxun"
WANGXI = "hero.jie_lidian.wangxi"

INDULGENCE = cards_of_type(PACK, "indulgence")[0]


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _pass_or_first(query):
    """最小合法应答：响应 / 濒死窗口放弃，其余取候选里第一个。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    return query.legal_actions[0]


class Scripted:
    """``handler(query) -> Action | None`` 先跑；返回 ``None`` 时再按最小规则应答。"""

    def __init__(self, handler=None, *, activate: bool = True) -> None:
        self._handler = handler
        self.activate = activate
        self.activation_queries: list = []

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        if query.kind == "SKILL_ACTIVATION":
            self.activation_queries.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill):
                        return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
        return _pass_or_first(query)


def _activate(query, skill_id: str):
    if query.kind != "SKILL_ACTIVATION" or query.skill_id != skill_id:
        return None
    for action in query.legal_actions:
        if isinstance(action, ActivateSkill):
            return action
    return None


def _run(engine, players, stop):
    return run_queries(engine, players, stop)


def _kind_queries(engine, skill_id: str) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and record.query.skill_id == skill_id
    ]


def _queries_of_kind(engine, kind: str) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


# ---------------------------------------------------------------------------
# 伤害后技能的构造局面（与引擎压 DAMAGE 子帧的形状逐字相同）
# ---------------------------------------------------------------------------


def _damage_frame(state, card, *, target: int, amount: int, source: int | None):
    channel = state.card(card) if card is not None else None
    return {
        "source": source,
        "targets": (target,),
        "card": channel,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": target,
        "channel": channel,
        "nature": "NORMAL",
        "chain": False,
        "amount": amount,
    }


def _damage_engine(
    *,
    seats,
    source: int,
    target: int,
    amount: int = 1,
    deck: tuple[int, ...] | None = None,
    card: int | None = None,
    players=None,
):
    """压一个 ``DAMAGE`` 子帧并推进到 ``DAMAGE_END``（出栈）。"""
    if card is None:
        card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck if deck is not None else deck_excluding(PACK, card),
        processing=(card,),
    )
    engine = Engine.from_state(state, seed=7)
    if players is None:
        players = [Scripted() for _ in seats]
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, card, target=target, amount=amount, source=source),
        timing=Timing.DAMAGE_DEAL,
    )
    _run(engine, players, lambda e: ran_timing(e, Timing.DAMAGE_END))
    return engine, players


# ---------------------------------------------------------------------------
# ① 界郭嘉【天妒】：与标准版逐字相同 ⇒ 复用同一条流水线 / 同一条发动形态
# ---------------------------------------------------------------------------


def test_jie_tiandu_takes_the_judge_card_and_keeps_it_out_of_the_discard():
    """正例：自己的判定牌生效后，答发动 ⇒ 判定牌进手牌、不进弃牌堆。"""
    judge_card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=3, hp_max=3, hero="jie_guojia", judge=(INDULGENCE,)),
            SeatSpec(hp=4),
        ],
        deck=(judge_card, *deck_excluding(PACK, judge_card, INDULGENCE)),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    taker = Scripted(lambda query: _activate(query, JIE_TIANDU))
    _run(engine, [taker, Scripted()], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert _kind_queries(engine, JIE_TIANDU), "界郭嘉的判定牌生效后必须问【天妒】"
    assert judge_card in engine.state.player(0).hand, "生效后获得此判定牌"
    assert judge_card not in engine.state.discard, "已被获得 ⇒ 不再入弃牌堆"


def test_jie_tiandu_is_not_offered_when_the_judgment_is_someone_elses():
    """反例：判定牌不是界郭嘉的 ⇒ 连「是否发动」都不问。"""
    judge_card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, judge=(INDULGENCE,)),
            SeatSpec(hp=3, hp_max=3, hero="jie_guojia"),
        ],
        deck=(judge_card, *deck_excluding(PACK, judge_card, INDULGENCE)),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    _run(engine, [Scripted(), Scripted()], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert not _kind_queries(engine, JIE_TIANDU)
    assert judge_card in engine.state.discard


def test_jie_tiandu_reuses_the_standard_flow_and_activation_plan():
    """实现面：界【天妒】与标准版**共用**同一个 ``SkillFlow`` 对象与同一条发动形态。

    票面写"【天妒】与标准版逐字相同 ⇒ 复用"，这条把"复用"钉成可核对的同一性：
    两条定义指向同一个流水线对象，且各有一条「是否发动」形态（按判定帧的 subject 自校）。
    """
    from sgs.skills import CATALOG
    from sgs.skills.catalog import ACTIVATION_PLANS
    from sgs.skills.rejudge import TIANDU_FLOW

    standard = CATALOG["hero.guojia.tiandu"]
    jie = CATALOG[JIE_TIANDU]

    assert jie.hooks[0].flow is TIANDU_FLOW
    assert jie.hooks == standard.hooks
    assert jie.locked == standard.locked
    assert jie.requirement == standard.requirement
    assert ACTIVATION_PLANS[JIE_TIANDU] is ACTIVATION_PLANS["hero.guojia.tiandu"]


# ---------------------------------------------------------------------------
# ② 界郭嘉【遗计】：摸两张，然后把至多两张**手牌**交给至多两名其他角色
# ---------------------------------------------------------------------------


def _select_first_then(*, seats, picks=1):
    """应答【遗计】的选牌步：从面内取第一张；座位步按 ``seats`` 轮换。"""
    counter = {"round": 0}

    def handler(q):
        if q.kind == "SKILL_ACTIVATION" and q.skill_id == JIE_YIJI:
            return _activate(q, JIE_YIJI)
        if q.kind == "SELECT_CARDS":
            return SelectCards(answers_seq=q.seq, picks=tuple(range(picks)))
        if q.kind == "SELECT_OPTION":
            seat = seats[min(counter["round"], len(seats) - 1)]
            counter["round"] += 1
            return SelectOption(answers_seq=q.seq, choice=f"seat:{seat}")
        return None

    return handler


def test_jie_yiji_draws_two_then_distributes_hand_cards_to_other_characters():
    """正例：受伤 1 点 ⇒ 摸两张，然后把手牌交给**两名不同的其他角色**。"""
    top = (_cards("peach")[0], _cards("peach")[1])
    state_seats = [
        SeatSpec(hp=4),
        SeatSpec(hp=3, hp_max=3, hero="jie_guojia"),
        SeatSpec(hp=4),
    ]
    engine, _players = _damage_engine(
        seats=state_seats,
        source=0,
        target=1,
        deck=(*top, *deck_excluding(PACK, *top)),
        players=[
            Scripted(),
            Scripted(_select_first_then(seats=(0, 2))),
            Scripted(),
        ],
    )

    assert _kind_queries(engine, JIE_YIJI), "受伤后必须问界【遗计】"
    selects = _queries_of_kind(engine, "SELECT_CARDS")
    options = _queries_of_kind(engine, "SELECT_OPTION")
    assert selects and options, "分配走既有的选牌 + 选人询问（不新造私有 Action）"
    assert sum(len(query.slots) for query in selects) >= 2, "选牌面铺的是持有者的手牌"

    owner_hand = engine.state.player(1).hand
    assert len(owner_hand) == 0, "摸两张、两张都交出去"
    assert len(engine.state.player(0).hand) == 1, "一名其他角色各得一张"
    assert len(engine.state.player(2).hand) == 1, "至多两名其他角色"


def test_jie_yiji_can_stop_after_one_card_or_give_nothing():
    """反例（可选性）：分配是"至多两张"——放弃选牌 ⇒ 只摸两张、一张都不交。"""
    top = (_cards("peach")[0], _cards("peach")[1])
    engine, _players = _damage_engine(
        seats=[
            SeatSpec(hp=4),
            SeatSpec(hp=3, hp_max=3, hero="jie_guojia"),
        ],
        source=0,
        target=1,
        deck=(*top, *deck_excluding(PACK, *top)),
        players=[
            Scripted(),
            Scripted(lambda q: (
                _activate(q, JIE_YIJI)
                if q.kind == "SKILL_ACTIVATION" and q.skill_id == JIE_YIJI
                else (Pass(answers_seq=q.seq) if q.kind == "SELECT_CARDS" else None)
            )),
        ],
    )

    assert _kind_queries(engine, JIE_YIJI)
    assert sorted(engine.state.player(1).hand) == sorted(top), "只摸两张"
    assert engine.state.player(0).hand == [], "一张都没交出去"


def test_jie_yiji_is_not_offered_to_the_unhurt_owner():
    """反例（归属）：伤害打在别人身上 ⇒ 界郭嘉连「是否发动」都不问。"""
    engine, _players = _damage_engine(
        seats=[
            SeatSpec(hp=4),
            SeatSpec(hp=4),
            SeatSpec(hp=3, hp_max=3, hero="jie_guojia"),
        ],
        source=0,
        target=1,
    )

    assert not _kind_queries(engine, JIE_YIJI)


def test_jie_yiji_triggers_once_per_damage_point():
    """逐点："受到 1 点伤害后" ⇒ 受到 2 点伤害问两次、各摸两张。"""

    def activate_then_pass(q):
        if q.kind == "SKILL_ACTIVATION" and q.skill_id == JIE_YIJI:
            return _activate(q, JIE_YIJI)
        if q.kind == "SELECT_CARDS":
            return Pass(answers_seq=q.seq)
        return None

    engine, _players = _damage_engine(
        seats=[
            SeatSpec(hp=4),
            SeatSpec(hp=3, hp_max=3, hero="jie_guojia"),
        ],
        source=0,
        target=1,
        amount=2,
        players=[Scripted(), Scripted(activate_then_pass)],
    )

    assert len(_kind_queries(engine, JIE_YIJI)) == 2, "2 点伤害 ⇒ 两次「是否发动」"
    assert len(engine.state.player(1).hand) == 4, "每点各摸两张（分配放弃）"


# ---------------------------------------------------------------------------
# ③ 界李典【恂恂】：摸牌阶段开始时看四张，两张置顶（顺序可选）、其余置底
# ---------------------------------------------------------------------------


def test_xunxun_puts_two_chosen_cards_on_top_and_the_rest_on_the_bottom():
    """正例：固定切分 2 / 2，置顶两行内顺序可选；随后照常摸牌（摸到刚置顶的两张）。"""
    top = (_cards("slash")[0], _cards("peach")[0], _cards("dodge")[0], _cards("duel")[0])
    rest = deck_excluding(PACK, *top)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hero="jie_lidian"), SeatSpec(hp=4)],
        deck=(*top, *rest),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    def arrange(query):
        if query.kind == "ARRANGE":
            return ArrangeCards(answers_seq=query.seq, order=(2, 0, 3, 1), split=2)
        return _activate(query, XUNXUN)

    _run(engine, [Scripted(arrange), Scripted()], lambda e: ran_timing(e, Timing.PHASE_DRAW_END))

    assert _kind_queries(engine, XUNXUN), "摸牌阶段开始时必须问【恂恂】"
    query = _queries_of_kind(engine, "ARRANGE")
    assert len(query) == 1, "只发一次排列询问"
    assert len(query[0].revealed) == 4, "观看牌堆顶四张"

    assert engine.state.deck[-2:] == [top[3], top[1]], "其余两张接在牌堆底（顺序按玩家排的）"
    assert engine.state.player(0).hand == [top[2], top[0]], (
        "随后正常摸两张——摸到的正是刚置顶的两张，且按置顶顺序"
    )
    assert top[2] not in engine.state.deck and top[0] not in engine.state.deck, "置顶两张已被摸走"


def test_declining_xunxun_draws_the_original_top_two():
    """反例（可选性）：放弃发动 ⇒ 不发排列询问，照常摸原来的牌堆顶两张。"""
    top = (_cards("slash")[0], _cards("peach")[0], _cards("dodge")[0], _cards("duel")[0])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hero="jie_lidian"), SeatSpec(hp=4)],
        deck=(*top, *deck_excluding(PACK, *top)),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    _run(
        engine,
        [Scripted(activate=False), Scripted()],
        lambda e: ran_timing(e, Timing.PHASE_DRAW_END),
    )

    assert not _queries_of_kind(engine, "ARRANGE")
    assert sorted(engine.state.player(0).hand) == sorted(top[:2])
    assert engine.state.deck[:2] == [top[2], top[3]]


def test_xunxun_is_not_offered_outside_the_owners_draw_phase():
    """反例（归属）：别人的摸牌阶段 ⇒ 界李典连「是否发动」都不问。"""
    top = (_cards("slash")[0], _cards("peach")[0], _cards("dodge")[0], _cards("duel")[0])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4, hero="jie_lidian")],
        deck=(*top, *deck_excluding(PACK, *top)),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    _run(engine, [Scripted(), Scripted()], lambda e: ran_timing(e, Timing.PHASE_DRAW_END))

    assert not _kind_queries(engine, XUNXUN)
    assert not _queries_of_kind(engine, "ARRANGE")


# ---------------------------------------------------------------------------
# ④ 界李典【忘隙】：造成 / 受到 1 点伤害后摸两张并交给该角色其中一张
# ---------------------------------------------------------------------------


def _wangxi_handler(*, give_index: int = 0):
    def handler(query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == WANGXI:
            return _activate(query, WANGXI)
        if query.kind == "SELECT_CARDS":
            return SelectCards(answers_seq=query.seq, picks=(give_index,))
        return None

    return handler


def test_wangxi_after_taking_damage_draws_two_and_gives_one_to_the_source():
    """受到伤害一侧：摸两张、把其中一张交给**伤害来源**。"""
    top = (_cards("peach")[0], _cards("dodge")[0])
    engine, _players = _damage_engine(
        seats=[SeatSpec(hp=4), SeatSpec(hp=3, hp_max=3, hero="jie_lidian")],
        source=0,
        target=1,
        deck=(*top, *deck_excluding(PACK, *top)),
        players=[Scripted(), Scripted(_wangxi_handler())],
    )

    assert _kind_queries(engine, WANGXI), "受到 1 点伤害后必须问【忘隙】"
    assert sorted(engine.state.player(1).hand) == [top[1]], "摸两张、交给来源一张"
    assert engine.state.player(0).hand == [top[0]], "交出的那张进伤害来源的手牌"


def test_wangxi_after_dealing_damage_draws_two_and_gives_one_to_the_target():
    """造成伤害一侧：界李典造成 1 点伤害 ⇒ 摸两张、把其中一张交给**受伤角色**。"""
    top = (_cards("peach")[0], _cards("dodge")[0])
    engine, _players = _damage_engine(
        seats=[SeatSpec(hp=4, hero="jie_lidian"), SeatSpec(hp=4)],
        source=0,
        target=1,
        deck=(*top, *deck_excluding(PACK, *top)),
        players=[Scripted(_wangxi_handler()), Scripted()],
    )

    assert _kind_queries(engine, WANGXI), "造成 1 点伤害后必须问【忘隙】"
    assert sorted(engine.state.player(0).hand) == [top[1]], "摸两张、交给受伤角色一张"
    assert engine.state.player(1).hand == [top[0]]


def test_wangxi_triggers_once_per_damage_point_on_both_sides():
    """逐点：2 点伤害 ⇒ 造成 / 受到各问两次、各摸两张。"""
    dealing, _ = _damage_engine(
        seats=[SeatSpec(hp=4, hero="jie_lidian"), SeatSpec(hp=4)],
        source=0,
        target=1,
        amount=2,
        players=[Scripted(_wangxi_handler()), Scripted()],
    )
    taking, _ = _damage_engine(
        seats=[SeatSpec(hp=4), SeatSpec(hp=3, hp_max=3, hero="jie_lidian")],
        source=0,
        target=1,
        amount=2,
        players=[Scripted(), Scripted(_wangxi_handler())],
    )

    assert len(_kind_queries(dealing, WANGXI)) == 2, "造成 2 点 ⇒ 两个触发点"
    assert len(_kind_queries(taking, WANGXI)) == 2, "受到 2 点 ⇒ 两个触发点"
    assert len(dealing.state.player(0).hand) == 2, "两点各摸两张、各交给受伤角色一张"
    assert len(taking.state.player(1).hand) == 2


def test_wangxi_is_not_offered_for_damage_between_two_other_characters():
    """反例（归属）：没有界李典参与的伤害 ⇒ 不问。"""
    engine, _players = _damage_engine(
        seats=[
            SeatSpec(hp=4),
            SeatSpec(hp=4),
            SeatSpec(hp=3, hp_max=3, hero="jie_lidian"),
        ],
        source=0,
        target=1,
    )

    assert not _kind_queries(engine, WANGXI)


def test_wangxi_is_not_offered_for_sourceless_or_self_inflicted_damage():
    """反例（"其他角色"）：无来源伤害与自伤都不构成"其他角色造成 / 受到"。"""
    sourceless, _ = _damage_engine(
        seats=[SeatSpec(hp=3, hp_max=3, hero="jie_lidian"), SeatSpec(hp=4)],
        source=None,
        target=0,
    )
    self_harm, _ = _damage_engine(
        seats=[SeatSpec(hp=3, hp_max=3, hero="jie_lidian"), SeatSpec(hp=4)],
        source=0,
        target=0,
    )

    assert not _kind_queries(sourceless, WANGXI), "无来源伤害没有「该角色」可给"
    assert not _kind_queries(self_harm, WANGXI), "自己不是「其他角色」"
