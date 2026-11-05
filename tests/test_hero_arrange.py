"""G 族：观星与授予可见性（票 10）。

落点与票 10 §验收一致，全部走**外部缝**：

- **协议缝**：``QueryKind.ARRANGE`` / ``Action.ArrangeCards`` / ``_QueryBase.revealed``——
  授予通道是**查询级**的（不写状态、不进 ``PlayerView``），观星与将来的【遗计】共用同一条；
- **引擎缝**：``Engine.from_state`` 的人为局面 + 脚本玩家驱动的**记录流**与终局状态
  （``state.deck`` 的自顶向下顺序就是观星排完的结果）；
- **不泄露缝**：同一次询问的 ``view.deck_count`` 照旧、其它座位的 ``PlayerView`` 里看不到
  被授予的牌、位置由 ``ArrangeCards`` 行动（只带面内位置）承载而不另发带牌面的 delta。

判据这样读：**牌堆顺序**是排完的直接事实，故主判据是 ``state.deck``；"谁看见了什么"读
询问记录里的 ``view`` 与 ``revealed``；"没泄露"读**其它**座位询问的 ``PlayerView`` 与
记录流里有没有带这些牌面的 ``card_move``。
"""

from __future__ import annotations

from itertools import permutations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    ArrangeContext,
    ArrangeQuery,
    EndPhase,
    Pass,
    Phase,
    PlayPhaseQuery,
    QueryKind,
    SelectOptionContext,
    parse_action,
    project,
)
from sgs.timing import Timing

from support import SeatSpec, build_state

PACK = load_content_pack(deck_profile="standard_108")

GUANXING = "hero.zhugeliang.guanxing"
ZHUGELIANG = "zhugeliang"


def _view(state, seat: int = 0):
    return project(state, seat)


# ---------------------------------------------------------------------------
# ① 授予通道（协议缝）
# ---------------------------------------------------------------------------


def test_arrange_cards_is_a_parseable_action():
    """``ArrangeCards`` 是新的行动构造子：``order`` = 面内位置的排列、``split`` = 切分点。"""
    action = parse_action(
        {"kind": "ArrangeCards", "answers_seq": 3, "order": [1, 0, 2], "split": 1}
    )

    assert isinstance(action, ArrangeCards)
    assert action.order == (1, 0, 2)
    assert action.split == 1


def test_arrange_query_kind_exists():
    assert QueryKind.ARRANGE.value == "ARRANGE"


def test_a_query_without_a_grant_omits_revealed_from_the_wire():
    """空授予**不进序列化**：M2 的 34 条基准靠这条保持逐字节不变。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    query = PlayPhaseQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="出牌阶段。",
        legal_actions=(EndPhase(answers_seq=1),),
        view=_view(state),
    )

    assert "revealed" not in query.model_dump(mode="json")
    assert query.revealed == ()


def test_a_granted_query_carries_the_revealed_faces_and_round_trips():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    cards = state.refs([0, 1])
    query = ArrangeQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PREPARE,
        prompt="请排列牌堆顶的牌。",
        legal_actions=(ArrangeCards(order=(0, 1), split=0, answers_seq=1),),
        view=_view(state),
        revealed=cards,
    )

    dumped = query.model_dump(mode="json")

    assert dumped["revealed"] == [card.model_dump(mode="json") for card in cards]
    assert ArrangeQuery.model_validate(dumped) == query


# ---------------------------------------------------------------------------
# ② 排列候选（协议缝）
# ---------------------------------------------------------------------------


def test_every_arrangement_of_the_revealed_cards_is_offered():
    """候选 = X 个面内位置的**全部排列** × **全部切分点**（顶 k 张 / 底其余）。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    from sgs.effects import legal_actions_for

    actions = legal_actions_for(
        state,
        0,
        QueryKind.ARRANGE,
        answers_seq=7,
        payload=ArrangeContext(count=3),
    )

    assert len(actions) == 6 * 4, "3! 个排列 × (3+1) 个切分点"
    assert {action.order for action in actions} == set(permutations(range(3)))
    assert {action.split for action in actions} == {0, 1, 2, 3}
    assert all(isinstance(action, ArrangeCards) for action in actions)
    assert all(action.answers_seq == 7 for action in actions)


# ---------------------------------------------------------------------------
# ③ 诸葛亮【观星】（引擎缝）
# ---------------------------------------------------------------------------

DECK = (10, 11, 12, 13, 14, 15, 16, 17)


def _pass_or_end(query):
    """最小合法应答：响应 / 濒死窗口放弃、出牌阶段结束、其余取第一个候选。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    if query.kind == "PLAY_PHASE":
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    return query.legal_actions[0]


class GuanxingPlayer:
    """应答【观星】：是否发动、排列成指定的 ``order`` / ``split``；其余询问最小合法应答。"""

    def __init__(
        self,
        *,
        skill_id: str = GUANXING,
        activate: bool = True,
        order: tuple[int, ...] | None = None,
        split: int = 0,
    ) -> None:
        self.skill_id = skill_id
        self.activate = activate
        self.order = order
        self.split = split
        self.activation_query = None
        self.arrange_query = None
        self.arranged = False

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == self.skill_id:
            self.activation_query = query
            if not self.activate:
                return next(
                    action for action in query.legal_actions if isinstance(action, Pass)
                )
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
        if query.kind == "ARRANGE":
            self.arrange_query = query
            chosen = next(
                action
                for action in query.legal_actions
                if action.order == self.order and action.split == self.split
            )
            self.arranged = True
            return chosen
        return _pass_or_end(query)


def _run(engine: Engine, players, stop) -> Engine:
    """逐步推进到 ``stop(engine)`` 成立（同 ``tests/test_hero_move_after.py`` 的手写推进）。"""
    for _ in range(5_000):
        if stop(engine):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _after_prepare(engine: Engine) -> bool:
    """准备阶段体（含观星的看牌与排列）已经跑完：阶段推进到判定。"""
    return engine.state.phase is not None and engine.state.phase is not Phase.PREPARE


def _prepare_engine(*, hero: str, seats: int, deck=DECK, turn_seat: int = 0) -> Engine:
    specs = [SeatSpec(hero=hero)] + [SeatSpec() for _ in range(seats - 1)]
    state = build_state(pack=PACK, seats=specs, deck=deck, turn_seat=turn_seat)
    return Engine.from_state(state, seed=7)


def _run_guanxing(engine: Engine, player: GuanxingPlayer, *, seats: int) -> Engine:
    """推进到准备阶段体跑完（排列已放回牌堆），再返回引擎。"""
    return _run(engine, [player] * seats, _after_prepare)


def test_guanxing_reveals_the_top_x_cards_and_places_them_by_order_and_split():
    """一次观星：``revealed`` = 牌堆顶 X 张；排完后顶 / 底的相对顺序与切分点都由动作给定。"""
    engine = _prepare_engine(hero=ZHUGELIANG, seats=2)
    player = GuanxingPlayer(order=(1, 0), split=1)
    _run_guanxing(engine, player, seats=2)

    query = player.arrange_query

    assert isinstance(query, ArrangeQuery)
    assert tuple(card.card_id for card in query.revealed) == DECK[:2], "X = min(存活数, 5) = 2"
    # order=(1,0) / split=1 ⇒ 顶 = [位置 1]，底 = [位置 0]（其余按 order 顺序接在牌堆底之下）。
    assert engine.state.deck == [DECK[1], *DECK[2:], DECK[0]]


def test_guanxing_puts_the_remainder_below_the_existing_deck_bottom():
    """规则口径钉死：**置底 = 接在原有牌堆底之下**（追加到 ``state.deck`` 尾部）。"""
    deck = tuple(range(8))
    engine = _prepare_engine(hero=ZHUGELIANG, seats=2, deck=deck)
    # X = 2（2 人存活）。order=(1,0) / split=2 ⇒ 两张都置顶：位置 1 在上、位置 0 在下。
    player = GuanxingPlayer(order=(1, 0), split=2)
    _run_guanxing(engine, player, seats=2)

    assert engine.state.deck == [deck[1], deck[0], *deck[2:]]

    # split=0 ⇒ 两张都置底，按 order 的顺序接在原有牌堆底之下。
    engine = _prepare_engine(hero=ZHUGELIANG, seats=2, deck=deck)
    player = GuanxingPlayer(order=(1, 0), split=0)
    _run_guanxing(engine, player, seats=2)

    assert engine.state.deck == [*deck[2:], deck[1], deck[0]]


def test_guanxing_x_is_two_when_only_two_characters_are_alive():
    engine = _prepare_engine(hero=ZHUGELIANG, seats=2)
    player = GuanxingPlayer(order=(0, 1), split=2)
    _run_guanxing(engine, player, seats=2)

    assert len(player.arrange_query.revealed) == 2


def test_guanxing_x_is_capped_at_five_when_eight_characters_are_alive():
    engine = _prepare_engine(hero=ZHUGELIANG, seats=8)
    player = GuanxingPlayer(order=(4, 3, 2, 1, 0), split=5)
    _run_guanxing(engine, player, seats=8)

    assert tuple(card.card_id for card in player.arrange_query.revealed) == DECK[:5]
    # 全部置顶、倒序 ⇒ 牌堆顶的 5 张恰好反过来。
    assert engine.state.deck == [DECK[4], DECK[3], DECK[2], DECK[1], DECK[0], *DECK[5:]]


def test_declining_guanxing_leaves_the_deck_untouched_and_asks_no_arrangement():
    engine = _prepare_engine(hero=ZHUGELIANG, seats=2)
    player = GuanxingPlayer(activate=False)
    before = list(engine.state.deck)

    _run_guanxing(engine, player, seats=2)

    assert player.activation_query is not None, "非锁定技：先问'是否发动'"
    assert player.arrange_query is None, "答 Pass ⇒ 不开排列询问"
    assert engine.state.deck == before, "牌堆不动"


# ---------------------------------------------------------------------------
# ④ 授予不泄露（票 10 §验收 2）
# ---------------------------------------------------------------------------


def _ids(refs) -> tuple[int, ...]:
    return tuple(card.card_id for card in refs)


def test_the_grant_is_visible_only_in_the_granting_query_and_never_in_a_view_or_move():
    """授予是**逐次**的：牌面只出现在那一次询问的 ``revealed``，不落任何视角 / delta / 行动。"""
    engine = _prepare_engine(hero=ZHUGELIANG, seats=2)
    player = GuanxingPlayer(order=(1, 0), split=1)
    _run_guanxing(engine, player, seats=2)

    granted = _ids(player.arrange_query.revealed)
    assert granted == DECK[:2]
    assert player.arrange_query.view.deck_count == len(DECK), "授予不改牌堆张数"

    for record in engine.records:
        if record.kind == "query":
            if record.query.kind == "ARRANGE":
                continue
            assert record.query.revealed == (), "除被授予的那次询问外，任何询问都不带牌面"
            view = record.query.view
            assert not set(granted) & set(_ids(view.self_hand))
            assert not set(granted) & set(_ids(view.processing))
            assert not set(granted) & set(_ids(view.discard))
            for public in view.players:
                assert not set(granted) & set(_ids(public.equip))
                assert not set(granted) & set(_ids(public.judge))
        if record.kind == "delta" and hasattr(record, "cards"):
            assert not set(granted) & set(_ids(record.cards)), "排列不另发带牌面的 delta"

    # 其它座位的视角同样看不到：直接从活状态投影一次。
    other = project(engine.state, 1)
    assert not set(granted) & set(_ids(other.self_hand))
    assert not set(granted) & set(_ids(other.processing))
    assert not set(granted) & set(_ids(other.discard))
    assert all(not set(granted) & set(_ids(p.equip)) for p in other.players)


# ---------------------------------------------------------------------------
# ⑤ 授予通道是通用的（【遗计】只看不排，票 05 复用）
# ---------------------------------------------------------------------------


YIJI = "hero.guojia.yiji"


def _fake_yiji_handler(host, frame):
    """假【遗计】形状：观看牌堆顶 2 张、把牌面授予**另一次**询问（只看不排）。"""
    seat = frame.data["skill_owner"]
    cards = frame.data.get("yiji_cards")
    if cards is None:
        cards = host.peek_deck(frame, 2)
        frame.data["yiji_cards"] = cards
    answer = host.ask_query(
        QueryKind.SELECT_OPTION,
        seat,
        Timing.PHASE_PREPARE,
        payload=SelectOptionContext(skill_id=YIJI, options=("keep", "give")),
        revealed=cards,
    )
    if host.suspended(answer):
        return "stay"
    return None


class _OptionPlayer(GuanxingPlayer):
    """应答假【遗计】的 ``SELECT_OPTION``；其余走观星玩家那一套。"""

    def respond(self, query):
        if query.kind == "SELECT_OPTION" and query.skill_id == YIJI:
            self.option_query = query
            return query.legal_actions[0]
        return super().respond(query)


def test_a_non_arrange_query_can_be_granted_the_same_way(monkeypatch):
    """``revealed`` 挂在 ``_QueryBase`` 上——任何询问都能被授予，不只排列（票 10 §① 3）。"""
    from types import MappingProxyType

    from sgs.effects import CarrierKind
    from sgs.skills import Hook, SkillDef, SkillType
    from sgs.skills import catalog as catalog_module

    definition = SkillDef(
        skill_id=YIJI,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.NORMAL,
        hooks=(Hook(Timing.PHASE_PREPARE, _fake_yiji_handler),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, YIJI: definition}),
    )
    engine = _prepare_engine(hero="guojia", seats=2)
    player = _OptionPlayer()
    player.option_query = None
    _run(engine, [player, player], lambda _engine: player.option_query is not None)

    query = player.option_query

    assert query.kind == "SELECT_OPTION"
    assert _ids(query.revealed) == DECK[:2], "同一通道把牌面授予了另一次询问"
    assert query.view.deck_count == len(DECK)
    assert engine.state.deck == list(DECK), "只看不排"


# ---------------------------------------------------------------------------
# ⑥ 缺省参与者与 AI（排列候选对默认随机玩家可用；打分器归票 13）
# ---------------------------------------------------------------------------


def test_the_default_random_participant_can_answer_the_arrangement_query():
    """缺省参与者（均匀随机）不需要认识 ARRANGE：候选内联，挑一个即可答。

    票 13 才给 AI 装 ``ARRANGE`` 打分器；本票只保证**协议**对默认参与者是可答的，
    且整局能继续（一次观星不把对局卡死）。
    """
    import sgs.agents as agents
    from sgs.engine import ai_stream

    from support import run_turns

    class _ActivateThenRandom:
        def __init__(self, inner) -> None:
            self._inner = inner

        def respond(self, query):
            if query.kind == "SKILL_ACTIVATION" and query.skill_id == GUANXING:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return self._inner.respond(query)

    engine = _prepare_engine(hero=ZHUGELIANG, seats=2)
    players = [
        _ActivateThenRandom(agents.RandomPlayer(ai_stream(7, seat)))
        for seat in range(2)
    ]
    run_turns(engine, players, turns=1)

    assert any(
        record.kind == "action"
        and record.accepted
        and getattr(record.action, "kind", None) == "ArrangeCards"
        for record in engine.records
    ), "默认随机玩家答下了一次排列询问，对局继续"


# ---------------------------------------------------------------------------
# ⑦ W6-02：观星迁模块级 SkillFlow（一份 DeckTopArrange 声明；peek 只一次）
# ---------------------------------------------------------------------------


def test_the_peek_deck_happens_once_across_the_arrange_suspension(monkeypatch):
    """spec B3：``run_flow`` 挂起后从头重跑，但看的牌由**首跑**定格（帧私有缓存）。

    ``Engine.peek_deck`` 在牌堆不够时会重洗并落一条 ``deck_shuffle``；naive 的"重入再
    peek 一次"会把那次副作用做两遍。这里直接数调用次数：一次观星只 peek 一次。
    """
    engine = _prepare_engine(hero=ZHUGELIANG, seats=2)
    player = GuanxingPlayer(order=(1, 0), split=1)
    calls: list[int] = []
    original = Engine.peek_deck

    def counting(self, frame, n):
        calls.append(n)
        return original(self, frame, n)

    monkeypatch.setattr(Engine, "peek_deck", counting)
    _run_guanxing(engine, player, seats=2)

    assert calls == [2], "X = min(存活数, 5) = 2；重入读缓存、不再 peek"


def test_guanxing_is_a_registered_module_level_flow_with_one_shared_arrange_spec():
    """W6-02：观星 = **模块级** ``GUANXING_FLOW``（一步 ``Arrange``）＋ ``SkillDef.deck_arrange``。

    两处引用**同一个** ``DeckTopArrange`` 对象（一份声明，票 03 的投影读后者）；
    ``preactivated`` 仍 ``False``，且 ``manifest`` / ``preactivation_for`` 对含 ``Arrange``
    的流**不炸、返回空清单**（spec B2）——观星每帧问"是否发动"时都会走到。
    """
    from sgs.skills import Arrange as PackageArrange
    from sgs.skills import CATALOG, flow_for
    from sgs.skills.active import Arrange as ActiveArrange
    from sgs.skills.arrange import GUANXING_ARRANGE, GUANXING_FLOW
    from sgs.skills.flow import FREE_SPLIT, Arrange, DeckTopArrange, manifest
    from sgs.skills.preactivation import preactivation_for

    definition = CATALOG[GUANXING]
    flow = flow_for(GUANXING, Timing.PHASE_PREPARE)

    assert flow is GUANXING_FLOW, "模块级具名常量 + Hook 登记（test_ask_points 的机械闸）"
    assert isinstance(GUANXING_ARRANGE, DeckTopArrange)
    assert GUANXING_ARRANGE.split == FREE_SPLIT, "观星是自由切分"
    assert len(flow.steps) == 1 and isinstance(flow.steps[0], Arrange)
    assert flow.steps[0].spec is GUANXING_ARRANGE, "步与 SkillDef 一份声明，不是两份拷贝"
    assert definition.deck_arrange is GUANXING_ARRANGE
    assert definition.preactivated is False, "观星不翻预发动（manifest 表达不了排列）"
    assert definition.hooks[0].flow is GUANXING_FLOW
    assert definition.hooks[0].handler is None

    # S12：新步骤类型要同步 active 与包入口的重导出（同一个对象，不另建名字）。
    assert ActiveArrange is Arrange
    assert PackageArrange is Arrange

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    assert callable(GUANXING_ARRANGE.count)
    assert GUANXING_ARRANGE.count(state, None) == 2, "X = min(存活数, 5)；规格不读帧"
    assert manifest(GUANXING_FLOW, state, 0) == ()
    assert preactivation_for(state, 0, GUANXING, timing=Timing.PHASE_PREPARE) == ()

