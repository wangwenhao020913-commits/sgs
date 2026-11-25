"""票 27：多目标结算与普通锦囊（库门面主缝）。

断言全部落在**记录流与棋盘状态**上：时机 / delta / query / action、体力、区域、手牌数。
不碰帧私有 ``data`` / ``pc``，也不碰注册表内部结构。

**例外**（按票面要求直接摆中间态 / 直调回调，其余仍只读记录流与棋盘）：

- ``test_harvest_skips_a_target_that_died_before_its_pick``（flow-resume-step-cancel 票 09 §4）
  与 ``test_duel_a_dead_user_whose_turn_it_is_is_not_asked``（dead-actor-in-flight 票 02）用
  ``Engine.push_frame`` 摆一个"目标表已冻结、逐座游标还没走完"的 ``RESOLVE`` 帧（后者另写
  一次帧私有 ``op.duel_turn``），此后仍全走产品路径（C 段步骤表、真实搬牌与询问记录）；
- ``test_borrowed_sword_a_dead_holder_is_cancelled_not_handed_over``（dead-actor-in-flight
  票 02）直调 ``ops.resolve_borrowed_sword``：auto-b 判据只在"持刀者已死、武器还在装备区"
  这一中间态上可观测——引擎的目标存活闸会先把整段跳过；
- W5-02b 的 auto-b 宿主桩（``_BorrowedHost``）同理只覆盖这个回调本身。

写中间态 / 直调回调都只为让"死者那一座 / 已死的持刀者"这一面可判（随机局里那一幕只能靠撞）。

覆盖三组行为：

1. **多目标两段式**：B 段"时机外层、目标内层"、C 段一个 ``RESOLVE`` 帧 + 游标逐目标、
   目标顺序"从当前回合角色起逆时针"、目标减为 0 ⇒ 终止使用流程；
2. **"无效"的层次**：桃园结义对未受伤者仍被指定、到 C1 终止（故没有后续结算）；
3. **普通锦囊**：无中生有 / 过河拆桥 / 顺手牵羊 / 决斗 / 借刀杀人 / 南蛮入侵 / 万箭齐发 /
   五谷丰登，以及 RESPOND / SELECT_CARDS 两类询问的选择面。

场景用 ``tests/support/positions.py`` 的纯函数构造局面，由库 API 驱动到目标段落完成即止
（被动局面不会自然终局）。构造局面里的人数为测试便利，``RuleConfig`` 仍是合法配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from sgs.data import RuleConfig, load_content_pack
from sgs.effects import check_targets
from sgs.engine import Engine, IllegalActionError
from sgs.state import (
    AskPolicy,
    EndPhase,
    FrameKind,
    Pass,
    PlayCard,
    PlayPhaseQuery,
    QueryKind,
    RespondQuery,
    SelectCards,
    StepPool,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support.positions import (
    RecordingPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    run_until,
)

STANDARD = "standard_108"


@pytest.fixture(scope="module")
def pack():
    return load_content_pack(deck_profile=STANDARD)


# ---------------------------------------------------------------------------
# 驱动：一个座位一个应答器，命中目标牌后其余只走"结束阶段 / 放弃"
# ---------------------------------------------------------------------------


@dataclass
class _Plan:
    play: str
    respond: str = "pass"
    """RESPOND / DODGE 窗口的应答：``play`` = 打出第一张候选、``use`` = 使用第一张候选、
    ``pass`` = 放弃。"""

    pick: int = 0
    use_target: int | None = None
    play_target: int | None = None
    """出牌阶段里只选目标 = 它的那条 ``UseCard``（【火攻】指定别人的那一张）。"""
    designations: tuple[int, ...] = ()
    """借刀杀人的**附带指定** Bn（W5-02b）：入口只剩 ``designation_actions`` 池。"""

    played: bool = field(default=False)


class _Planned:
    def __init__(self, plan: _Plan) -> None:
        self.plan = plan

    def respond(self, query):
        plan = self.plan
        if query.kind == QueryKind.PLAY_PHASE:
            if not plan.played:
                for action in query.legal_actions:
                    if (
                        isinstance(action, UseCard)
                        and action.card.type == plan.play
                        and (
                            plan.play_target is None
                            or action.targets == (plan.play_target,)
                        )
                    ):
                        plan.played = True
                        return action
                # 借刀杀人的裸候选已收回（W5-02b）：合法动作是"牌 + A + Bn"三元组，
                # 只在询问的**内部池**里；测试脚本与真实入口同一条路。
                for action in getattr(query, "designation_actions", ()):
                    if (
                        isinstance(action, UseCard)
                        and action.card.type == plan.play
                        and action.designations == plan.designations
                    ):
                        plan.played = True
                        return action
            return _find(query, EndPhase)
        if query.kind == QueryKind.RESPOND:
            if plan.respond == "play":
                card = _first(query, PlayCard)
                if card is not None:
                    return card
            if plan.respond == "use":
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and (
                        plan.use_target is None or action.targets == (plan.use_target,)
                    ):
                        return action
            return _find(query, Pass)
        if query.kind == QueryKind.SELECT_CARDS:
            return query.legal_actions[plan.pick]
        if query.kind == QueryKind.DODGE and plan.respond == "use":
            card = _first(query, UseCard)
            if card is not None:
                return card
        if query.kind in (QueryKind.DODGE, QueryKind.PEACH):
            return _find(query, Pass)
        return query.legal_actions[0]


def _first(query, action_type):
    for action in query.legal_actions:
        if isinstance(action, action_type):
            return action
    return None


def _find(query, action_type):
    action = _first(query, action_type)
    assert action is not None, f"{query.kind} 询问里没有 {action_type.__name__} 候选"
    return action


def _deck_excluding(pack, *card_ids: int) -> tuple[int, ...]:
    """除被指定的牌之外的全部牌实例（与手牌 / 装备区 / 判定区不相交）。"""
    excluded = set(card_ids)
    return tuple(index for index in range(len(pack.deck.cards)) if index not in excluded)


@dataclass(frozen=True)
class _DeathAt:
    """**在哪一问让谁阵亡**（dead-actor-in-flight 票 02 的构造入口）。

    ``on_seat`` = 触发死亡的询问应答者（脚本挂在这个座位上）；``victim`` = 阵亡者；
    ``kind`` / ``cause`` 把触发收窄到某一类询问（缺省 = 第一问）。"谁被问"与"谁死"分列
    是为了复现真实时机：【闪】窗口 / 【火攻】亮牌都问目标，可死的是使用者。
    """

    on_seat: int
    victim: int
    kind: QueryKind | None = None
    cause: WindowCause | None = None


class _DieAtQuery:
    """让 ``victim`` 在**第一处命中**的询问处阵亡（走产品死亡入口，不模拟清区）。

    死亡的真实时机之一是响应窗口里的代价（票 92 的现场就是【蛊惑】质疑的失体力代价在
    响应窗口里打死了使用者）。这里走**产品死亡入口** :meth:`Engine.enter_death`：把
    DEATH 帧压在当前 RESOLVE 帧之上，死亡结算先跑（清空三区），随后 C 段面对"参与者已
    阵亡"的状态——与真实死亡同一面，不靠测试自己模拟清区。
    """

    def __init__(self, inner, engine: Engine, victim: int, *, kind, cause) -> None:
        self._inner = inner
        self._engine = engine
        self._victim = victim
        self._kind = kind
        self._cause = cause
        self.died = False

    def _matches(self, query) -> bool:
        return (
            (self._kind is None or query.kind == self._kind)
            and (self._cause is None or getattr(query, "cause", None) is self._cause)
        )

    def respond(self, query):
        if not self.died and self._matches(query):
            self._engine.enter_death(self._engine.stack.top, self._victim)
            self.died = True
        return self._inner.respond(query)


def _run_trick(
    pack,
    *,
    play: str,
    seats: list[SeatSpec],
    deck: tuple[int, ...] | None = None,
    turn_seat: int = 0,
    seed: int = 1,
    respond: str = "pass",
    pick: int = 0,
    use_target: int | None = None,
    play_target: int | None = None,
    designations: tuple[int, ...] = (),
    die_during_nullify: bool = False,
    die_at: _DeathAt | None = None,
    ask_policy: AskPolicy | None = None,
) -> tuple[Engine, _Plan]:
    """构造局面 → 跑到"这张牌的使用结算结束后"即止。

    ``die_during_nullify`` = 使用者在无懈窗口阵亡（``turn_seat``）；``die_at`` 是更一般的
    写法（任意座位 / 任意询问类型）。``ask_policy`` 缺省 = 引擎缺省 OFF（照问）。
    """
    plan = _Plan(
        play=play,
        respond=respond,
        pick=pick,
        use_target=use_target,
        play_target=play_target,
        designations=designations,
    )
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=deck if deck is not None else _deck_excluding(pack, *used),
        turn_seat=turn_seat,
        rule_config=RuleConfig(first_seat=turn_seat),
    )
    engine = Engine.from_state(
        state, seed=seed, ask_policy=AskPolicy() if ask_policy is None else ask_policy
    )
    players = [_Planned(plan) for _ in seats]
    if die_during_nullify:
        die_at = _DeathAt(
            on_seat=turn_seat,
            victim=turn_seat,
            kind=QueryKind.RESPOND,
            cause=WindowCause.NULLIFY,
        )
    if die_at is not None:
        players[die_at.on_seat] = _DieAtQuery(
            players[die_at.on_seat],
            engine,
            die_at.victim,
            kind=die_at.kind,
            cause=die_at.cause,
        )

    run_until(engine, players, lambda e: plan.played and _use_settled(e, play))
    return engine, plan


def _use_settled(engine: Engine, play: str) -> bool:
    return any(
        record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and any(card.type == play for card in record.cards)
        for record in engine.records
    )


def _timings(engine: Engine, timing: Timing) -> list:
    return [r for r in engine.records if r.kind == "timing" and r.timing is timing]


def _queries(engine: Engine, kind: QueryKind) -> list:
    return [r.query for r in engine.records if r.kind == "query" and r.query.kind == kind]


def _windows(engine: Engine, cause: WindowCause) -> list:
    """某成因的响应窗口；票 28 起每张锦囊的"生效前"还会开一层无懈窗口，故按成因取。"""
    return [query for query in _queries(engine, QueryKind.RESPOND) if query.cause is cause]


def _deltas(engine: Engine, delta_type: str) -> list:
    return [r for r in engine.records if r.kind == "delta" and r.delta_type == delta_type]


def _resolve_frame_id(engine: Engine) -> int:
    return next(
        record.frame_id
        for record in engine.records
        if record.kind == "frame" and record.frame_kind.value == "RESOLVE"
    )


# ---------------------------------------------------------------------------
# 协议扩充
# ---------------------------------------------------------------------------


def test_the_ten_query_kinds_and_the_window_causes_are_defined():
    """票 30 把 ``QueryKind`` 从 6 类扩到 8 类（+ 发动 / 选项），票 10 再加 ``ARRANGE``（排列），
    huo-shenzhouyu-mobile 票 05 再加 ``DISTRIBUTE``（计数式分配）；jie-feng-batch2 票 01a 再加
    ``SELECT_OPTIONS``（多选选项）；M1 四类与票 27 两类不动（闭集只许增，与 ``WindowCause``
    同一姿态）。"""
    assert {kind.value for kind in QueryKind} == {
        "PLAY_PHASE",
        "DODGE",
        "PEACH",
        "DISCARD",
        "RESPOND",
        "SELECT_CARDS",
        "SKILL_ACTIVATION",
        "SELECT_OPTION",
        # jie-feng-batch2 票 01a：多选选项面（界夏侯渊【神速】「至多三项」）。
        "SELECT_OPTIONS",
        "ARRANGE",
        "DISTRIBUTE",
    }
    assert {cause.value for cause in WindowCause} == {
        "NULLIFY",
        "BARBARIAN_INVASION",
        "ARROW_BARRAGE",
        "DUEL",
        "BORROWED_SWORD",
        "LORD_REQUEST",
        "REJUDGE",
        # lin-batch2 票 03：贾诩移动版【乱武】的"对距离最近的另一名角色使用一张【杀】"
        # 复用借刀那扇 ``RESPOND`` 面（``mode=USE``），只多一个成因值——它与
        # ``BORROWED_SWORD`` 的区别是"放弃 = 失去 1 点体力"而不是"交出武器"（投影文案与
        # `pending.js:alternativeLabel` 各有一个分支）。
        "LUANWU",
    }


def test_play_card_and_select_cards_parse_through_the_single_entry():
    from sgs.state import CardRef, parse_action

    slash = CardRef(card_id=0, type="slash", name="杀", suit="spade", rank=7)
    played = parse_action({"kind": "PlayCard", "card": slash.model_dump(), "answers_seq": 3})
    picked = parse_action({"kind": "SelectCards", "picks": [1], "answers_seq": 4})

    assert isinstance(played, PlayCard) and played.answers_seq == 3
    assert isinstance(picked, SelectCards) and picked.picks == (1,)


# ---------------------------------------------------------------------------
# 第 0 层合法性（目标集合）
# ---------------------------------------------------------------------------


def test_dismantlement_needs_cards_in_the_targets_zones(pack):
    from sgs.effects import check_targets

    dismantle = cards_of_type(pack, "dismantlement")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(hand=(cards_of_type(pack, "slash")[0],)), SeatSpec()],
        turn_seat=0,
        rule_config=RuleConfig(),
    )

    assert check_targets(state, 0, state.card(dismantle)) == (1,)


def test_snatch_is_capped_at_distance_one(pack):
    from sgs.effects import check_targets

    snatch = cards_of_type(pack, "snatch")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(hand=(1,)), SeatSpec(hand=(2,)), SeatSpec(hand=(3,))],
        turn_seat=0,
        rule_config=RuleConfig(),
    )

    assert check_targets(state, 0, state.card(snatch)) == (1, 3)


def test_borrowed_sword_needs_a_weapon_and_a_legal_slash_target(pack):
    from sgs.effects import check_targets

    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(equip=(weapon,)), SeatSpec(), SeatSpec()],
        turn_seat=0,
        rule_config=RuleConfig(),
    )

    assert check_targets(state, 0, state.card(borrowed)) == (1,)


def test_use_card_candidates_are_ordered_by_their_explicit_targets(pack):
    from sgs.effects import legal_actions_for

    slash = cards_of_type(pack, "slash")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec(), SeatSpec()],
        turn_seat=0,
        rule_config=RuleConfig(),
    )
    uses = [
        action
        for action in legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, UseCard)
    ]

    assert [action.targets for action in uses] == [(1,), (3,)]


# ---------------------------------------------------------------------------
# 多目标两段式：B 段时机外层 / C 段帧内游标
# ---------------------------------------------------------------------------


def _barbarian_engine(pack, *, turn_seat: int, seats: list[SeatSpec]):
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    seats = list(seats)
    holder = seats[turn_seat]
    seats[turn_seat] = SeatSpec(hp=holder.hp, hp_max=holder.hp_max, hand=(barbarian,))
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    return _run_trick(
        pack,
        play="barbarian_invasion",
        seats=seats,
        deck=_deck_excluding(pack, *used),
        turn_seat=turn_seat,
        respond="pass",
    )


def test_a_multi_target_use_runs_one_resolve_frame_with_a_cursor_per_target(pack):
    engine, _ = _barbarian_engine(
        pack, turn_seat=0, seats=[SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec()]
    )

    pushes = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind.value == "RESOLVE"
    ]
    assert len(pushes) == 1, "多目标只有一个 RESOLVE 帧（票 15 §4）"

    frame_id = pushes[0].frame_id
    starts = [
        record for record in _timings(engine, Timing.RESOLVE_START) if record.frame_id == frame_id
    ]
    assert [record.targets for record in starts] == [(1,), (2,), (3,)]
    assert all(record.actor == 0 for record in starts), "C1 的 actor 保持使用者"
    assert [record.actor for record in _timings(engine, Timing.USE_BECOME_TARGET)] == [
        1,
        2,
        3,
    ], "B 段也是时机外层、目标内层"


def test_every_target_gets_its_own_resolution_before_the_next(pack):
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="barbarian_invasion",
        seats=[SeatSpec(hand=(barbarian,)), SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, barbarian, slash),
        turn_seat=0,
        respond="play",
    )

    frame_id = _resolve_frame_id(engine)
    stages = [
        record.timing
        for record in engine.records
        if record.kind == "timing"
        and record.frame_id == frame_id
        and record.timing.value.startswith("RESOLVE_")
    ]
    per_target = [
        Timing.RESOLVE_START,
        Timing.RESOLVE_BEFORE_EFFECT,
        Timing.RESOLVE_EFFECT,
        Timing.RESOLVE_AFTER_EFFECT,
        Timing.RESOLVE_END,
    ]
    assert stages == per_target * 3, "一个目标连续跑完它的所有 C 段时机，再轮到下一个"


def test_target_order_is_counterclockwise_from_the_turn_seat(pack):
    engine, _ = _barbarian_engine(
        pack, turn_seat=1, seats=[SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec()]
    )
    frame_id = _resolve_frame_id(engine)
    order = [
        record.targets[0]
        for record in _timings(engine, Timing.RESOLVE_START)
        if record.frame_id == frame_id
    ]

    assert order == [2, 3, 4, 0], "从当前回合角色（座位 1）起逆时针"


def test_an_empty_target_list_terminates_the_use_flow(pack):
    """目标减为 0 ⇒ 不压 RESOLVE、实体牌直接进弃牌堆（票 11 §5）。"""
    slash = cards_of_type(pack, "slash")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, slash),
        turn_seat=0,
        rule_config=RuleConfig(),
    )
    engine = Engine.from_state(state, seed=1)
    engine.start_use(state.card(slash), source=0, targets=())
    while engine.stack:
        engine._step()

    assert not _timings(engine, Timing.RESOLVE_START), "空目标不进入逐目标结算"
    assert _timings(engine, Timing.RESOLVE_AFTER_END), "仍走'使用结算结束后'"
    assert engine.state.discard == [slash]


# ---------------------------------------------------------------------------
# 第 2 层"无效"：桃园结义对未受伤者
# ---------------------------------------------------------------------------


def test_peach_garden_targets_the_unwounded_but_terminates_at_resolve_start(pack):
    garden = cards_of_type(pack, "peach_garden")[0]
    engine, _ = _run_trick(
        pack,
        play="peach_garden",
        seats=[SeatSpec(hp=4, hand=(garden,)), SeatSpec(hp=4), SeatSpec(hp=2)],
        deck=_deck_excluding(pack, garden),
        turn_seat=0,
    )

    frame_id = _resolve_frame_id(engine)
    starts = [
        r.targets[0] for r in _timings(engine, Timing.RESOLVE_START) if r.frame_id == frame_id
    ]
    after = [
        r.targets[0]
        for r in _timings(engine, Timing.RESOLVE_AFTER_EFFECT)
        if r.frame_id == frame_id
    ]
    recover = [r.targets[0] for r in _timings(engine, Timing.RECOVER_VALUE)]

    assert starts == [0, 1, 2], "未受伤者仍被指定"
    assert after == [2], "只有受伤者继续结算（未受伤者在 C1 终止）"
    assert recover == [2]
    assert [player.hp for player in engine.state.players] == [4, 4, 3]


# ---------------------------------------------------------------------------
# 普通锦囊
# ---------------------------------------------------------------------------


def test_ex_nihilo_draws_two_cards(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[SeatSpec(hand=(ex_nihilo,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, ex_nihilo),
        turn_seat=0,
    )
    frame_id = _resolve_frame_id(engine)
    draws = [
        delta
        for delta in _deltas(engine, "card_move")
        if delta.reason.value == "DRAW"
        and delta.to_zone.zone is Zone.HAND
        and delta.frame_id == frame_id
    ]

    assert [len(delta.cards) for delta in draws] == [2]


def test_dismantlement_discards_a_hidden_hand_card_by_slot_position(pack):
    dismantle = cards_of_type(pack, "dismantlement")[0]
    victim_card = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="dismantlement",
        seats=[SeatSpec(hand=(dismantle,)), SeatSpec(hand=(victim_card,)), SeatSpec()],
        deck=_deck_excluding(pack, dismantle, victim_card),
        turn_seat=0,
    )

    selections = _queries(engine, QueryKind.SELECT_CARDS)
    assert len(selections) == 1
    assert [(slot.ref.zone, slot.card) for slot in selections[0].slots] == [(Zone.HAND, None)], (
        "他人手牌是隐藏槽：只有位置、没有牌面身份"
    )
    picks = [
        r.action.picks
        for r in engine.records
        if r.kind == "action" and r.action.kind == "SelectCards"
    ]
    assert picks == [(0,)], "行动用面内位置引用"
    assert victim_card in engine.state.discard
    assert engine.state.player(1).hand == []


def test_snatch_takes_a_visible_equipment_card_into_hand(pack):
    snatch = cards_of_type(pack, "snatch")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    engine, _ = _run_trick(
        pack,
        play="snatch",
        seats=[SeatSpec(hand=(snatch,)), SeatSpec(), SeatSpec(), SeatSpec(equip=(weapon,))],
        deck=_deck_excluding(pack, snatch, weapon),
        turn_seat=0,
    )

    slots = _queries(engine, QueryKind.SELECT_CARDS)[0].slots
    assert [(slot.ref.zone, slot.card.card_id) for slot in slots] == [(Zone.EQUIP, weapon)]
    assert weapon in engine.state.player(0).hand
    assert engine.state.player(3).equip == []


def _run_constructed(engine: Engine, seats: int) -> None:
    """把一个人造中间态跑到结算栈清空（最多 2 000 步，防"永远收不了尾"吊死测试）。"""
    players = [RecordingPlayer() for _ in range(seats)]
    for _ in range(2_000):
        if not engine.stack.frames:
            return
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("构造局面的结算在 2 000 步内没有跑完")


def test_a_snatch_user_who_dies_in_flight_asks_nobody_and_grants_nothing(pack):
    """顺手牵羊的使用者在无懈窗口阵亡（票 92 现场）：询问取消、不派生"获得"、牌留原处。

    选牌询问对**已阵亡的使用者**返回 ``Cancelled``（既不是 ``Pass`` 也不是空选），
    因此"获得"这一段不执行；目标装备区的牌留原处；使用牌照常进弃牌堆。
    """
    snatch = cards_of_type(pack, "snatch")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    engine, _ = _run_trick(
        pack,
        play="snatch",
        seats=[SeatSpec(hand=(snatch,)), SeatSpec(), SeatSpec(), SeatSpec(equip=(weapon,))],
        deck=_deck_excluding(pack, snatch, weapon),
        turn_seat=0,
        die_during_nullify=True,
    )

    assert _queries(engine, QueryKind.SELECT_CARDS) == [], "窗口外不向死者要选择"
    assert weapon in engine.state.player(3).equip, "目标牌留原处（不派生『获得』）"
    assert engine.state.player(3).hand == []
    assert snatch in engine.state.discard, "使用牌照常进弃牌堆"
    dead = engine.state.player(0)
    assert (dead.hand, dead.equip, dead.judge) == ([], [], []), "死者三区空"


def test_a_dismantlement_user_who_dies_in_flight_leaves_the_card_in_place(pack):
    """过河拆桥的使用者在无懈窗口阵亡：选牌询问取消，"弃置"不执行、目标牌留原处。"""
    dismantle = cards_of_type(pack, "dismantlement")[0]
    victim = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="dismantlement",
        seats=[SeatSpec(hand=(dismantle,)), SeatSpec(hand=(victim,)), SeatSpec()],
        deck=_deck_excluding(pack, dismantle, victim),
        turn_seat=0,
        die_during_nullify=True,
    )

    assert _queries(engine, QueryKind.SELECT_CARDS) == [], "窗口外不向死者要选择"
    assert victim in engine.state.player(1).hand, "目标牌留原处（不派生『弃置』）"
    assert victim not in engine.state.discard
    assert dismantle in engine.state.discard, "使用牌照常进弃牌堆"
    dead = engine.state.player(0)
    assert (dead.hand, dead.equip, dead.judge) == ([], [], []), "死者三区空"


def test_a_dead_target_of_a_multi_target_trick_is_skipped_while_the_others_respond(pack):
    """目标存活：目标表冻结后目标阵亡 ⇒ 该目标那一份不做，其余目标照跑。

    RESOLVE 帧的 ``targets = (0, 2)``（座位 0 已阵亡）：座位 0 不接受"南蛮"的响应询问、
    也不落伤害 / 濒死；座位 2 照常被问、放弃后受到来源 1 的 1 点伤害。
    """
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(alive=False, hp=0), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, barbarian),
        processing=(barbarian,),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=1)
    engine.push_frame(
        FrameKind.RESOLVE,
        {
            "card": state.card(barbarian),
            "source": 1,
            "targets": (0, 2),
            "cursor": 0,
        },
        timing=Timing.RESOLVE_START,
    )
    _run_constructed(engine, 3)

    responses = _windows(engine, WindowCause.BARBARIAN_INVASION)
    assert [query.actor for query in responses] == [2], "死目标不被问、活目标照常响应"
    assert engine.state.player(0).hp == 0, "死目标不落伤害、不进入濒死"
    assert engine.state.player(2).hp == 3, "活目标受到来源 1 的 1 点伤害"
    assert not [delta for delta in _deltas(engine, "hp_change") if delta.seat == 0]


def test_damage_from_a_dead_source_is_pushed_without_a_source(pack):
    """伤害来源已阵亡 ⇒ 压 DAMAGE 帧时 ``source=None``（业炎口径推广；已压出的帧不追溯）。"""
    arrows = cards_of_type(pack, "arrow_barrage")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(alive=False, hp=0), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, arrows),
        processing=(arrows,),
        turn_seat=2,
    )
    engine = Engine.from_state(state, seed=1)
    engine.push_frame(
        FrameKind.RESOLVE,
        {
            "card": state.card(arrows),
            "source": 0,
            "targets": (1,),
            "cursor": 0,
        },
        timing=Timing.RESOLVE_START,
    )
    _run_constructed(engine, 3)

    assert engine.state.player(1).hp == 3, "目标照常受到 1 点伤害"
    assert engine.damage_events[-1].source is None, "压帧时来源已阵亡 ⇒ 无来源伤害"
    deals = _timings(engine, Timing.DAMAGE_DEAL)
    assert deals and deals[-1].actor is None, "DAMAGE 时机的 actor 也不认死者"


def test_barbarian_invasion_only_hits_who_cannot_play_a_slash(pack):
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="barbarian_invasion",
        seats=[SeatSpec(hand=(barbarian,)), SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, barbarian, slash),
        turn_seat=0,
        respond="play",
    )

    assert [player.hp for player in engine.state.players] == [4, 4, 3, 3]
    responses = _windows(engine, WindowCause.BARBARIAN_INVASION)
    assert {query.required for query in responses} == {"slash"}
    assert slash in engine.state.discard, "打出的【杀】进弃牌堆"


def test_arrow_barrage_needs_a_dodge(pack):
    arrows = cards_of_type(pack, "arrow_barrage")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    engine, _ = _run_trick(
        pack,
        play="arrow_barrage",
        seats=[SeatSpec(hand=(arrows,)), SeatSpec(hand=(dodge,)), SeatSpec()],
        deck=_deck_excluding(pack, arrows, dodge),
        turn_seat=0,
        respond="play",
    )

    assert [player.hp for player in engine.state.players] == [4, 4, 3]
    assert {query.required for query in _windows(engine, WindowCause.ARROW_BARRAGE)} == {"dodge"}


def test_duel_alternates_slash_until_someone_cannot_play(pack):
    duel = cards_of_type(pack, "duel")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="duel",
        seats=[SeatSpec(hand=(duel,)), SeatSpec(hand=(slash,)), SeatSpec()],
        deck=_deck_excluding(pack, duel, slash),
        turn_seat=0,
        respond="play",
    )

    windows = _windows(engine, WindowCause.DUEL)
    assert [query.cause for query in windows] == [WindowCause.DUEL, WindowCause.DUEL]
    assert [query.target for query in windows] == [None, None], "打出窗口的 target 为 None"
    # 目标先出【杀】，使用者无【杀】⇒ 使用者受到目标造成的 1 点伤害。
    assert [player.hp for player in engine.state.players] == [3, 4, 4]


def test_borrowed_sword_lets_the_holder_use_a_slash(pack):
    """使用者按清单指定 [持刀者 A=1, Bn=2]；持刀者选 a（使用【杀】）⇒ 不交出武器。"""
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="borrowed_sword",
        seats=[SeatSpec(hand=(borrowed,)), SeatSpec(hand=(slash,), equip=(weapon,)), SeatSpec()],
        deck=_deck_excluding(pack, borrowed, weapon, slash),
        turn_seat=0,
        respond="use",
        use_target=2,
        designations=(2,),
    )

    window = _windows(engine, WindowCause.BORROWED_SWORD)[0]
    assert window.cause is WindowCause.BORROWED_SWORD
    assert window.target == 1, "借刀窗口针对持刀者（具体座位）"
    assert any(isinstance(action, UseCard) for action in window.legal_actions)
    uses = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind.value == "USE"
    ]
    assert len(uses) == 2, "借刀本身 + 持刀者使用的【杀】"
    assert weapon in engine.state.player(1).equip, "使用【杀】后不交出武器"
    assert engine.state.player(2).hp == 3, "被指定的 Bn 受到【杀】的伤害"


def test_borrowed_sword_hands_over_the_weapon_when_refused(pack):
    """b = ``Pass``：持刀者**能**使用【杀】却主动放弃 ⇒ 武器进使用者手牌。"""
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="borrowed_sword",
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(slash,), equip=(weapon,)),
            SeatSpec(),
        ],
        deck=_deck_excluding(pack, borrowed, weapon, slash),
        turn_seat=0,
        respond="pass",
        designations=(2,),
    )

    assert weapon in engine.state.player(0).hand
    assert engine.state.player(1).equip == []
    assert slash in engine.state.player(1).hand, "主动放弃 ⇒ 那张【杀】没被用掉"


# ---------------------------------------------------------------------------
# W5-04：次数限制只在持刀者**自己的出牌阶段**生效（用户裁定 2026-10-30）
# ---------------------------------------------------------------------------


def _borrowed_in_another_turn(
    pack, *, holder_used: int, holder_slashes: int = 2, borrowed_count: int = 1
):
    """座位 0 的回合：手里 ``borrowed_count`` 张借刀；座位 1 持刀（雌雄双股剑）+ 若干【杀】。

    ``holder_used`` = 持刀者**自己上一回合**用掉的【杀】数，故意留在 ``slash`` 计数键上——
    W5-04 的实机根因就是那个陈旧计数被"别人回合里的借刀窗口"读到了。
    攻击范围 2 在 3 人环上覆盖 0 / 2 两个座位，Bn = 2 恒为合法【杀】目标。
    """
    borrowed = cards_of_type(pack, "borrowed_sword")[:borrowed_count]
    weapon = cards_of_type(pack, "double_sword")[0]
    slashes = cards_of_type(pack, "slash")[:holder_slashes]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=tuple(borrowed)),
            SeatSpec(hand=tuple(slashes), equip=(weapon,)),
            SeatSpec(),
        ],
        deck=_deck_excluding(pack, *borrowed, weapon, *slashes),
        turn_seat=0,
        rule_config=RuleConfig(first_seat=0),
    )
    state.player(1).card_use_counts["slash"] = holder_used
    return Engine.from_state(state, seed=1), weapon, slashes


def _pass_players(count: int = 3) -> list[_Planned]:
    """只回答"结束阶段 / 放弃"的脚本玩家（座位数 = ``count``）。"""
    return [_Planned(_Plan(play="__none__", played=True)) for _ in range(count)]


def _borrowed_designation(
    query, *, designations: tuple[int, ...] = (2,)
) -> UseCard | None:
    """本次出牌阶段询问的内部池里"借刀 + [持刀者 1, Bn]"那条动作（不在池里则 ``None``）。"""
    for action in getattr(query, "designation_actions", ()):
        if isinstance(action, UseCard) and action.designations == designations:
            return action
    return None


def _submit_borrowed(engine, *, designations: tuple[int, ...] = (2,)) -> None:
    """停在座位 0 的出牌阶段询问上 ⇒ 提交"借刀 + [持刀者 1, Bn]"。"""
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery), f"应停在出牌阶段询问，实得 {query!r}"
    action = _borrowed_designation(query, designations=designations)
    assert action is not None, "本次出牌阶段询问里没有 [持刀者 1, Bn] 的借刀动作"
    engine.submit(action)


def test_a_slash_used_in_an_earlier_turn_does_not_close_the_borrowed_sword_window(pack):
    """W5-04：持刀者**上一回合**用过【杀】，本回合（座位 0 的回合）被借刀。

    借刀窗口必须仍给出"对 Bn 使用【杀】"的候选（不是 auto-b）；持刀者由此不交出武器。
    先红时以 ``xfail(strict=True)`` 落地（``08f222a``），修复提交摘掉标记。
    """
    engine, weapon, _slashes = _borrowed_in_another_turn(pack, holder_used=1)
    players = _pass_players()
    run_until(engine, players, lambda e: e.pending is not None)
    _submit_borrowed(engine)
    run_until(
        engine,
        players,
        lambda e: bool(_windows(e, WindowCause.BORROWED_SWORD))
        or weapon not in e.state.player(1).equip,
    )

    window = engine.pending
    assert isinstance(window, RespondQuery) and window.cause is WindowCause.BORROWED_SWORD, (
        "陈旧的【杀】计数不得把 a 变成不可行（auto-b）：必须开窗口问 a / b"
    )
    uses = [action for action in window.legal_actions if isinstance(action, UseCard)]
    assert uses and {action.targets for action in uses} == {(2,)}, (
        "a = 持刀者对 Bn 使用一张【杀】"
    )

    engine.submit(uses[0])
    run_until(engine, players, lambda e: _use_settled(e, "slash"))

    assert weapon in engine.state.player(1).equip, "出【杀】者不交出武器"
    assert engine.state.player(2).hp == 3, "Bn 受到 1 点伤害"


class _BorrowRepeatedly:
    """座位 0 的出牌阶段脚本：每次都用一张借刀（指定 [持刀者 1, Bn 2]），没有就结束阶段。"""

    def __init__(self, *, designations: tuple[int, ...] = (2,)) -> None:
        self.designations = designations
        self.plays = 0

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            action = _borrowed_designation(query, designations=self.designations)
            if action is not None:
                self.plays += 1
                return action
            return _find(query, EndPhase)
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _slash_uses_settled(engine) -> int:
    """已结算完的【杀】张数（按 ``RESOLVE_AFTER_END`` 的牌面事实数，借刀本身不算）。"""
    return sum(
        1
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and any(card.type == "slash" for card in record.cards)
    )


def test_two_borrowed_swords_in_one_other_turn_both_let_the_holder_use_a_slash(pack):
    """用户裁定："在其他角色回合不考虑出杀次数"——同一个别人的回合里两次借刀都允许。

    第二次借刀时持刀者的 ``slash`` 计数已经是 1（第一次被迫使用的【杀】记在同一个键上）；
    按裁定它仍不是限制条件，故两刀都打得出去、武器不交出。
    """
    engine, weapon, _slashes = _borrowed_in_another_turn(
        pack, holder_used=0, holder_slashes=2, borrowed_count=2
    )
    borrower = _BorrowRepeatedly()
    holder = _Planned(_Plan(play="__none__", played=True, respond="use", use_target=2))
    players = [borrower, holder, *_pass_players(1)]

    run_until(
        engine,
        players,
        lambda e: _slash_uses_settled(e) >= 2 or weapon not in e.state.player(1).equip,
    )

    assert borrower.plays == 2, "座位 0 的两张借刀都用了"
    assert len(_windows(engine, WindowCause.BORROWED_SWORD)) == 2, (
        "同一个别人的回合里两次借刀都要问持刀者 a / b"
    )
    assert _slash_uses_settled(engine) == 2, "两刀都真的打出去了"
    assert engine.state.player(2).hp == 2, "Bn 连吃两刀"
    assert weapon in engine.state.player(1).equip, "两次都出【杀】⇒ 武器不交出"


# ---------------------------------------------------------------------------
# W5-02a 借刀：附带指定（暗发布）
# ---------------------------------------------------------------------------


def _borrowed_designation_engine(pack):
    """座位 1 持刀（雌雄双股剑，攻击范围 2）；座位 0 手里一张借刀。

    4 个座位让座位 1 的攻击范围覆盖 0 / 2 / 3——旧路径的 Bn 候选因此有 3 个，
    新路径固定到其中一个才有可观测差别。
    """
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    seats = [
        SeatSpec(hand=(borrowed,)),
        SeatSpec(hand=(slash,), equip=(weapon,)),
        SeatSpec(),
        SeatSpec(),
    ]
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=_deck_excluding(pack, *used),
        turn_seat=0,
        rule_config=RuleConfig(first_seat=0),
    )
    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [RecordingPlayer() for _ in seats],
        lambda e: e.pending is not None,
    )
    assert isinstance(engine.pending, PlayPhaseQuery)
    return engine, borrowed


def _finish_borrowed_window(engine) -> None:
    """从已提交的借刀继续跑到**持刀者使用的【杀】结算结束**。"""
    players = [
        _Planned(_Plan(play="__none__", respond="use", played=True)) for _ in range(4)
    ]
    run_until(engine, players, lambda e: _use_settled(e, "slash"))


def test_the_borrowed_sword_designation_pool_and_manifest_shape(pack):
    engine, borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)

    pool = query.designation_actions
    assert {(action.targets, action.designations) for action in pool} == {
        ((1,), (0,)),
        ((1,), (2,)),
        ((1,), (3,)),
    }, "每张借刀 × 合法持刀者 A × A 攻击范围内的每个 Bn"
    assert all(action.card.card_id == borrowed for action in pool)
    assert all(action.answers_seq == query.seq for action in pool), "池与本次询问同源"

    steps = query.card_preactivations["borrowed_sword"]
    assert [(step.name, step.min, step.max, step.order) for step in steps] == [
        ("seat", 1, 1, 0),
        ("seat", 1, 1, 1),
    ], "两步清单：持刀者 → Bn"
    assert steps[0].wording == "选择持刀者（装备区有武器、且有合法【杀】目标）"
    assert steps[1].wording == "选择【杀】的目标（由你指定）"
    assert steps[0].pool == (StepPool(when=(), options=("seat:1",)),)
    assert steps[1].pool == (
        StepPool(when=("seat:1",), options=("seat:0", "seat:2", "seat:3")),
    ), "Bn 子池按 when='seat:A' 分列"

    assert "designation_actions" not in query.model_dump(), "内部池不进记录流"
    assert "card_preactivations" not in query.model_dump(), "清单不进记录流"
    assert not any(
        isinstance(action, UseCard) and action.card.type == "borrowed_sword"
        for action in query.legal_actions
    ), "W5-02b：裸借刀候选已从 legal_actions 收回（入口只剩清单）"
    assert "borrowed_sword" in query.card_preactivations, "可用性只剩这份两步清单"


def test_a_submitted_designation_must_be_a_member_of_the_pool(pack):
    engine, _borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    action = next(
        candidate for candidate in query.designation_actions if candidate.designations == (2,)
    )

    engine.submit(action)

    assert engine.pending is None
    accepted = engine.records[-1]
    assert accepted.kind == "action" and accepted.accepted is True
    assert accepted.action == action
    assert accepted.action.designations == (2,)


def test_a_designation_outside_the_pool_is_rejected_and_changes_nothing(pack):
    engine, _borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    candidate = query.designation_actions[0]
    tampered = UseCard(
        card=candidate.card,
        targets=candidate.targets,
        designations=(99,),
        answers_seq=query.seq,
    )
    baseline = (engine.state.model_dump(), engine.stack.depth, engine.finished)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(tampered)

    assert caught.value.reason_code == "IllegalAction"
    assert "候选池" in caught.value.message
    assert (engine.state.model_dump(), engine.stack.depth, engine.finished) == baseline, (
        "附带指定校验失败必须零状态改动"
    )
    assert engine.pending is query
    rejected = engine.records[-1]
    assert rejected.kind == "action" and rejected.accepted is False
    assert rejected.action == tampered
    assert rejected.reason is not None and "附带指定" in rejected.reason


def test_a_designation_fixes_the_holder_windows_target_to_bn(pack):
    engine, _borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    action = next(
        candidate for candidate in query.designation_actions if candidate.designations == (2,)
    )
    engine.submit(action)

    _finish_borrowed_window(engine)

    window = _windows(engine, WindowCause.BORROWED_SWORD)[0]
    assert window.target == 1, "窗口仍问持刀者"
    assert {
        candidate.targets
        for candidate in window.legal_actions
        if isinstance(candidate, UseCard)
    } == {(2,)}, "有 designation 时目标固定为 Bn，持刀者只选 a / b"
    assert [player.hp for player in engine.state.players] == [4, 4, 3, 4]


def _borrowed_halberd_engine(pack, *, holder_extra: tuple[int, ...] = ()):
    """座位 1 = 持刀者，装备【方天画戟】、手里**恰好一张**【杀】（+ ``holder_extra``）。

    4 个座位 ⇒ 座位 1 的【杀】池是 {0, 2, 3}：0 是使用者指定的 Bn，2 / 3 是两个额外目标。
    ``holder_extra`` 非空 ⇒ 这张【杀】不再是"最后一张手牌"，方天不成立（对照组）。
    """
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    halberd = cards_of_type(pack, "halberd")[0]
    slash = cards_of_type(pack, "slash")[0]
    seats = [
        SeatSpec(hand=(borrowed,)),
        SeatSpec(hand=(slash, *holder_extra), equip=(halberd,)),
        SeatSpec(),
        SeatSpec(),
    ]
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=_deck_excluding(pack, *used),
        turn_seat=0,
        rule_config=RuleConfig(first_seat=0),
    )
    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [RecordingPlayer() for _ in seats],
        lambda e: e.pending is not None,
    )
    assert isinstance(engine.pending, PlayPhaseQuery)
    return engine, borrowed, slash


class _MaxTargetsHolder:
    """借刀窗口里选**目标数最多**的【杀】候选（W7-01 的 3 目标组合）；其余同 ``_Planned``。"""

    def __init__(self) -> None:
        self._rest = _Planned(_Plan(play="__none__", respond="use", played=True))

    def respond(self, query):
        if (
            query.kind == QueryKind.RESPOND
            and getattr(query, "cause", None) is WindowCause.BORROWED_SWORD
        ):
            slashes = [
                action
                for action in query.legal_actions
                if isinstance(action, UseCard) and action.card.type == "slash"
            ]
            assert slashes, "持刀者窗口必须有 a（使用【杀】）"
            return max(slashes, key=lambda action: (len(action.targets), action.targets))
        return self._rest.respond(query)


def test_the_borrowed_sword_holder_with_the_halberd_can_add_two_more_targets(pack):
    """借刀 × 方天（W7-01）：持刀者的【杀】恒含 Bn，另加 0..2 个其它合法目标。

    主体分列照旧：**外层**借刀的 ``designations=(Bn,)`` 不变；**持刀者的【杀】**
    ``designations`` 为空，Bn 与额外目标都在 ``targets`` 里。结算断言三名目标各受 1 点
    伤害（全场都放弃【闪】）。
    """
    engine, _borrowed, slash = _borrowed_halberd_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    action = next(
        candidate for candidate in query.designation_actions if candidate.designations == (0,)
    )
    engine.submit(action)

    before = len(_queries(engine, QueryKind.PLAY_PHASE))
    run_until(
        engine,
        [
            _Planned(_Plan(play="__none__", played=True)),
            _MaxTargetsHolder(),
            _Planned(_Plan(play="__none__", played=True)),
            _Planned(_Plan(play="__none__", played=True)),
        ],
        lambda e: len(_queries(e, QueryKind.PLAY_PHASE)) > before,
    )

    window = _windows(engine, WindowCause.BORROWED_SWORD)[0]
    assert window.target == 1, "窗口仍问持刀者"
    offers = {
        candidate.targets
        for candidate in window.legal_actions
        if isinstance(candidate, UseCard) and candidate.card.type == "slash"
    }
    assert offers == {(0,), (0, 2), (0, 3), (0, 2, 3)}, "Bn + 0..2 个其它合法目标"
    assert all(0 in targets for targets in offers), "每条候选都恒含 Bn"
    pool = check_targets(engine.state, 1, engine.state.card(slash))
    assert all(set(combo) <= set(pool) for combo in offers), "额外目标过同一个 check_targets 池"

    accepted = [
        record.action
        for record in engine.records
        if record.kind == "action" and record.accepted and isinstance(record.action, UseCard)
    ]
    outer = next(card for card in accepted if card.card.type == "borrowed_sword")
    assert outer.targets == (1,), "外层借刀的目标仍只有持刀者"
    assert outer.designations == (0,), "外层附带指定不变（W5 语义）"
    holder_slash = next(card for card in accepted if card.card.type == "slash")
    assert holder_slash.designations == (), "持刀者的【杀】**没有**附带指定"
    assert holder_slash.targets == (0, 2, 3), "Bn 与两个额外目标都在 targets 里"
    assert [player.hp for player in engine.state.players] == [3, 4, 3, 3], (
        "三名目标各结算一次【杀】"
    )


def test_the_halberd_needs_the_slash_to_be_the_last_hand_card_in_the_borrow_window(pack):
    """对照组：持刀者手里还有别的牌 ⇒ 方天不成立，借刀窗口仍只固定 Bn（W5 形状不变）。"""
    extra = cards_of_type(pack, "peach")[0]
    engine, _borrowed, _slash = _borrowed_halberd_engine(pack, holder_extra=(extra,))
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    action = next(
        candidate for candidate in query.designation_actions if candidate.designations == (0,)
    )
    engine.submit(action)

    run_until(
        engine,
        [_Planned(_Plan(play="__none__", played=True)) for _ in range(4)],
        lambda e: bool(_windows(e, WindowCause.BORROWED_SWORD)),
    )

    window = _windows(engine, WindowCause.BORROWED_SWORD)[0]
    offers = {
        candidate.targets
        for candidate in window.legal_actions
        if isinstance(candidate, UseCard) and candidate.card.type == "slash"
    }
    assert offers == {(0,)}, "不是最后一张手牌 ⇒ 最大目标数仍为 1"


def test_the_bare_borrowed_sword_is_no_longer_offered_or_submittable(pack):
    """W5-02b 翻转：裸候选**收回**；仍然提交裸动作 ⇒ `IllegalAction` + 零状态改动。

    旧路径（持刀者自选 Bn）已删除：``resolve_borrowed_sword`` 要求恰好一个 designation，
    缺失即 fail-closed。入口因此只剩清单，提交也必须带 ``designations``。
    """
    engine, borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    assert not any(
        isinstance(action, UseCard) and action.card.type == "borrowed_sword"
        for action in query.legal_actions
    ), "裸借刀候选不得再出现在 legal_actions"

    plain = UseCard(
        card=engine.state.card(borrowed), targets=(1,), answers_seq=query.seq
    )
    baseline = (engine.state.model_dump(), engine.stack.depth, engine.finished)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(plain)

    assert caught.value.reason_code == "IllegalAction"
    assert "候选集合" in caught.value.message
    assert (engine.state.model_dump(), engine.stack.depth, engine.finished) == baseline, (
        "旧路径的裸动作被拒后必须零状态改动"
    )
    assert engine.pending is query


def test_the_borrowed_sword_is_unavailable_when_the_manifest_is_empty(pack):
    """可用性只看清单：没有合法持刀者 ⇒ ``card_preactivations`` 为空，牌不可用。

    不 fail-open：清单渲不出来就是此刻不可用（声明错误应当可见），也不许退回裸候选。
    """
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(borrowed,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, borrowed),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [RecordingPlayer() for _ in range(3)],
        lambda e: e.pending is not None,
    )
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)

    assert query.card_preactivations == {}, "没有合法持刀者 ⇒ 清单为空"
    assert query.designation_actions == (), "内部池同样为空"
    assert not any(
        isinstance(action, UseCard) and action.card.type == "borrowed_sword"
        for action in query.legal_actions
    ), "不可用时也不得退回裸候选（不 fail-open）"


# ---------------------------------------------------------------------------
# W5-02b：b 支与结算层的 fail-closed / no-op（``ops.resolve_borrowed_sword``）
# ---------------------------------------------------------------------------


class _BorrowedHost:
    """``resolve_borrowed_sword`` 的最小宿主：只回答"有没有 a / 武器还在不在 / 持刀者活没活"。"""

    def __init__(self, *, offers: tuple = (), weapon=None) -> None:
        self.offers = tuple(offers)
        self.weapon = weapon
        self.moves: list[dict] = []
        self.asked = 0
        # 持刀者存活（auto-b 用例的前提）；"持刀者已死"那一面用真引擎构造（见票 02 的
        # ``test_borrowed_sword_a_dead_holder_is_cancelled_not_handed_over``）。
        self.state = SimpleNamespace(player=lambda seat: SimpleNamespace(alive=True))

    def current_target(self, frame):
        return 1

    def advance_provider(self, frame, timing):
        return None, None, None

    def respond_context(self, frame, actor, **kwargs):
        from sgs.state import RespondContext

        # 真窗口用 `Engine.respond_context` 建载荷；本宿主把它原样装回一个真的
        # `RespondContext`——预览与（若发生的）发问收的是同一个对象。
        return RespondContext(**kwargs)

    def respond_candidates(self, actor, ctx):
        return self.offers

    def ask_respond(self, frame, actor, timing, ctx):
        self.asked += 1
        raise AssertionError("auto-b：没有非 Pass 候选就不许发询问")

    def weapon_of(self, seat):
        return self.weapon

    def move_in_frame(self, frame, cards, **kwargs):
        self.moves.append({"cards": tuple(cards), **kwargs})
        return "moved"


def _borrowed_frame(*designations):
    from sgs.resolution import Frame

    return Frame(
        kind=FrameKind.RESOLVE,
        data={"source": 0, "designations": tuple(designations)},
    )


def test_the_borrowed_sword_resolution_requires_exactly_one_designation():
    """fail-closed：入口只经清单，缺失 / 多于一个即显式报错（不回落到持刀者自选）。

    用 ``ValueError`` 而不是 ``assert``：``python -O`` 会删掉断言，而这条是产品路径上的
    编程错误，必须无论优化级别都兜住。
    """
    from sgs.effects.ops import resolve_borrowed_sword

    host = _BorrowedHost()

    for designations in ((), (1, 2)):
        with pytest.raises(ValueError):
            resolve_borrowed_sword(host, _borrowed_frame(*designations))

    assert host.asked == 0 and host.moves == [], "报错在问 / 搬牌之前"


def test_the_borrowed_sword_b_branch_is_a_no_op_when_the_weapon_is_already_gone():
    """a 不可行 ⇒ 不问；武器已离装备区 ⇒ 什么都不做、也不报错。"""
    from sgs.effects.ops import resolve_borrowed_sword

    host = _BorrowedHost(offers=(), weapon=None)

    assert resolve_borrowed_sword(host, _borrowed_frame(2)) is None
    assert host.asked == 0, "a 不可行（非 Pass 候选为空）⇒ 自动 b，不发询问"
    assert host.moves == [], "武器已离区 ⇒ no-op"


def test_harvest_reveals_target_count_and_each_target_picks_one(pack):
    harvest = cards_of_type(pack, "harvest")[0]
    engine, _ = _run_trick(
        pack,
        play="harvest",
        seats=[SeatSpec(hand=(harvest,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, harvest),
        turn_seat=0,
    )

    revealed = [
        delta
        for delta in _deltas(engine, "card_move")
        if delta.to_zone.zone is Zone.PROCESSING and delta.from_zone.zone is Zone.DECK
    ]
    assert [len(delta.cards) for delta in revealed] == [3], "X = 目标数"
    reveal = _timings(engine, Timing.USE_AFTER_DESIGNATE_TARGET)
    assert reveal and reveal[0].seq < _timings(engine, Timing.RESOLVE_START)[0].seq, (
        "亮牌发生在'指定目标后'（B5），早于 C 段"
    )

    selections = _queries(engine, QueryKind.SELECT_CARDS)
    assert [query.actor for query in selections] == [0, 1, 2], "从当前回合角色起逆时针、含自己"
    assert [len(query.slots) for query in selections] == [3, 2, 1], "前一个取走后池子变小"
    assert [len(player.hand) for player in engine.state.players] == [3, 1, 1]
    assert engine.state.processing == [], "取完后处理区清空"


def test_harvest_revealed_cards_end_in_hands_and_processing_is_emptied(pack):
    harvest = cards_of_type(pack, "harvest")[0]
    engine, _ = _run_trick(
        pack,
        play="harvest",
        seats=[SeatSpec(hand=(harvest,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, harvest),
        turn_seat=0,
    )
    after_end = [
        record
        for record in _timings(engine, Timing.RESOLVE_AFTER_END)
        if any(card.type == "harvest" for card in record.cards)
    ]
    assert after_end, "使用结算结束后已到"
    assert engine.state.processing == []
    assert harvest in engine.state.discard


def test_harvest_skips_a_target_that_died_before_its_pick(pack):
    """回归（flow-resume-step-cancel 票 09）：逐座取牌队列里**死者不再被问、也不拿牌**。

    现场是门禁 fuzz 的 seed=15962758650526724301 / 7 人（seq=2263）：五谷丰登的亮牌队列
    里 3 名角色，其中 1 名在轮到自己取牌前阵亡，旧实现仍向死者发 ``SELECT_CARDS`` 并把
    一张牌 ``PUT`` 进死者手牌（"阵亡者三区为空"判红）。目标表在"使用时"就冻结，死者不会
    自己从队列里消失，故过滤点在 :func:`sgs.effects.ops.pick_harvest`（与濒死求桃的
    "活人环"同一纪律）：跳过它，它那一份留在处理区由 ``finish_harvest`` 一并弃掉。

    局面按票面 §4 的写法**直接摆成中间态**：``build_state`` 把三张亮牌放进处理区，再用
    :meth:`~sgs.engine.Engine.push_frame` 压一个"目标表已冻结、游标还没走完"的 ``RESOLVE``
    帧（座位 1 已阵亡，座位 0 / 2 存活）。此后**全部走产品路径**：C1–C5 的步骤表、真实
    搬牌、真实询问记录。判据三面——**谁被问**（询问记录里没有 1）、**牌落在哪**（两活人
    各一张进手牌）、**死者的手牌区**（空）、**死者那一份**（留在处理区）。
    """
    harvest = cards_of_type(pack, "harvest")[0]
    revealed = tuple(
        card_id for card_id in range(len(pack.deck.cards)) if card_id != harvest
    )[:3]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(alive=False, hp=0), SeatSpec()],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in (*revealed, harvest)
        ),
        processing=revealed,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.RESOLVE,
        {
            "card": state.card(harvest),
            "source": 0,
            "targets": (0, 1, 2),
            "cursor": 0,
            "revealed": revealed,
        },
        timing=Timing.RESOLVE_START,
    )
    players = [RecordingPlayer(), RecordingPlayer(), RecordingPlayer()]
    for _ in range(500):
        if not engine.stack.frames:
            break
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))

    selections = _queries(engine, QueryKind.SELECT_CARDS)
    assert [query.actor for query in selections] == [0, 2], (
        "已阵亡的座位 1 不该被问（「活人环」纪律）"
    )
    assert not [
        record.query for record in engine.records
        if record.kind == "query" and record.query.actor == 1
    ], "死者在这次结算里一条询问都不该收到"
    assert len(engine.state.player(0).hand) == 1, "座位 0 取到一张"
    assert len(engine.state.player(2).hand) == 1, "座位 2 取到一张"
    assert engine.state.player(1).hand == [], "死者的手牌区必须是空的"
    taken = (*engine.state.player(0).hand, *engine.state.player(2).hand)
    assert len(engine.state.processing) == 1, (
        "死者那一份留在处理区，由 finish_harvest 一并弃掉（不转给下家、不占份额）"
    )
    assert engine.state.processing[0] not in taken, "留下的那张不该被别的座位取走"


def test_harvest_terminates_the_use_when_the_reveal_cannot_complete(pack):
    harvest = cards_of_type(pack, "harvest")[0]
    other = [i for i in range(len(pack.deck.cards)) if i != harvest]
    engine, _ = _run_trick(
        pack,
        play="harvest",
        seats=[SeatSpec(hand=(harvest,)), SeatSpec(), SeatSpec()],
        # 摸牌阶段先取 2 张，只剩 1 张 < X = 3 ⇒ 执行动作未完整执行。
        deck=tuple(other[:3]),
        turn_seat=0,
    )

    assert not _timings(engine, Timing.RESOLVE_START), "终止使用结算、不进入逐目标结算"
    assert not _queries(engine, QueryKind.SELECT_CARDS), "不构造选牌询问"
    assert _timings(engine, Timing.RESOLVE_AFTER_END), "仍走'使用结算结束后'"
    assert not [r for r in engine.records if r.kind == "game_end"], "执行动作未完成不判平局"
    assert engine.state.processing == [], "已亮出的牌进弃牌堆"


def test_a_hidden_selection_face_is_reproducible_for_the_same_seed(pack):
    dismantle = cards_of_type(pack, "dismantlement")[0]
    victim_hand = tuple(cards_of_type(pack, "slash")[:3])
    deck = _deck_excluding(pack, dismantle, *victim_hand)
    seats = [SeatSpec(hand=(dismantle,)), SeatSpec(hand=victim_hand), SeatSpec()]

    first, _ = _run_trick(
        pack, play="dismantlement", seats=seats, deck=deck, turn_seat=0, seed=99
    )
    second, _ = _run_trick(
        pack, play="dismantlement", seats=seats, deck=deck, turn_seat=0, seed=99
    )

    assert first.journal() == second.journal()


def test_a_selection_query_round_trips_through_the_journal(pack, tmp_path):
    """选择面随询问落盘：canonical JSONL 往返后仍逐字节相等，隐藏槽仍无牌面身份。"""
    from sgs.engine import dump_journal, read_journal

    dismantle = cards_of_type(pack, "dismantlement")[0]
    victim = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="dismantlement",
        seats=[SeatSpec(hand=(dismantle,)), SeatSpec(hand=(victim,)), SeatSpec()],
        deck=_deck_excluding(pack, dismantle, victim),
        turn_seat=0,
    )

    text = dump_journal(engine.records)
    path = tmp_path / "trick.jsonl"
    path.write_text(text, encoding="utf-8")
    restored = read_journal(path)

    assert dump_journal(restored) == text
    selection = next(
        record.query
        for record in restored
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_CARDS
    )
    assert selection.slots[0].ref.zone is Zone.HAND
    assert selection.slots[0].card is None


# ---------------------------------------------------------------------------
# 阵亡后已在飞的牌侧结算（dead-actor-in-flight 票 02，spec §1.4 第 2–11 行）
#
# 每个"⇒"一个断言函数；借刀"持刀者死"与"持刀者 Pass"结果不同，分别命名。
# 死亡构造一律走产品入口 `Engine.enter_death`（见 `_DieAtQuery`），只在"死在哪一问 /
# 死的是谁"上做文章；断言仍只读记录流与棋盘。
# ---------------------------------------------------------------------------


def test_harvest_a_dead_user_leaves_the_others_picking_and_discards_its_share(pack):
    """五谷：使用者死 ⇒ 其余目标照常取牌；死目标跳过，它那一份留处理区、收尾弃掉。"""
    harvest = cards_of_type(pack, "harvest")[0]
    engine, _ = _run_trick(
        pack,
        play="harvest",
        seats=[SeatSpec(hand=(harvest,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, harvest),
        turn_seat=0,
        die_during_nullify=True,
    )

    selections = _queries(engine, QueryKind.SELECT_CARDS)
    assert [query.actor for query in selections] == [1, 2], (
        "其余目标照常取牌；已阵亡的使用者不再被问（目标存活闸逐目标判）"
    )
    revealed = tuple(
        card.card_id
        for delta in _deltas(engine, "card_move")
        if delta.to_zone.zone is Zone.PROCESSING and delta.from_zone.zone is Zone.DECK
        for card in delta.cards
    )
    assert len(revealed) == 3, "B5 亮牌照常（X = 目标数）"
    taken = tuple(
        card.card_id
        for delta in _deltas(engine, "card_move")
        if delta.from_zone.zone is Zone.PROCESSING and delta.to_zone.zone is Zone.HAND
        for card in delta.cards
    )
    assert len(taken) == 2, "两个活目标各取一张（取牌动作按真实搬牌读，不看整手牌）"
    assert set(taken) <= set(revealed)
    skipped = set(revealed) - set(taken)
    assert len(skipped) == 1, "死者那一份不转给下家、也不占份额"
    assert skipped <= set(engine.state.discard), "它那一份收尾进弃牌堆"
    assert engine.state.player(0).hand == [], "死者的手牌区为空"
    assert engine.state.processing == [], "收尾后处理区清空"
    assert harvest in engine.state.discard, "使用牌照常进弃牌堆"


def test_borrowed_sword_a_dead_user_still_lets_the_holder_use_the_slash(pack):
    """借刀：使用者死 + 持刀者出杀 ⇒ 杀照常结算（来源 = 持刀者）。"""
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="borrowed_sword",
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(slash,), equip=(weapon,)),
            SeatSpec(),
        ],
        deck=_deck_excluding(pack, borrowed, weapon, slash),
        turn_seat=0,
        respond="use",
        use_target=2,
        designations=(2,),
        die_during_nullify=True,
    )

    windows = _windows(engine, WindowCause.BORROWED_SWORD)
    assert [query.actor for query in windows] == [1], "使用者已死，持刀者照常被问 a / b"
    assert engine.state.player(2).hp == 3, "被迫的【杀】照常结算"
    assert engine.damage_events[-1].target == 2
    assert engine.damage_events[-1].source == 1, "来源 = 持刀者（不是已死的使用者）"
    assert weapon in engine.state.player(1).equip, "出【杀】者不交出武器"
    assert borrowed in engine.state.discard, "使用牌照常进弃牌堆"


def test_borrowed_sword_a_dead_user_does_not_receive_the_weapon_when_the_holder_passes(pack):
    """借刀：持刀者 `Pass` ⇒ 交武器给死者**不落地**（授予口），武器留在原处。"""
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="borrowed_sword",
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(slash,), equip=(weapon,)),
            SeatSpec(),
        ],
        deck=_deck_excluding(pack, borrowed, weapon, slash),
        turn_seat=0,
        respond="pass",
        designations=(2,),
        die_during_nullify=True,
    )

    assert [query.actor for query in _windows(engine, WindowCause.BORROWED_SWORD)] == [1], (
        "持刀者照常被问 a / b（`Pass` = 已接受的一次裁决）"
    )
    assert weapon in engine.state.player(1).equip, (
        "『否则交武器』向死者授予 ⇒ 不落地；武器留在持刀者装备区"
    )
    resolve_frame = _resolve_frame_id(engine)
    assert not [
        delta
        for delta in _deltas(engine, "card_move")
        if delta.frame_id == resolve_frame
        and delta.to_zone.zone is Zone.HAND
        and delta.to_zone.seat == 0
    ], "这次结算里没有任何牌被搬进死者的手牌（摸牌阶段的摸牌不算）"
    assert engine.state.player(0).hand == []
    assert slash in engine.state.player(1).hand, "主动放弃 ⇒ 那张【杀】没被用掉"
    assert borrowed in engine.state.discard


def test_borrowed_sword_a_holder_who_dies_in_flight_loses_the_weapon_to_the_discard(pack):
    """借刀：持刀者死 ⇒ 询问取消（不是 `Pass`）⇒「否则」不派生；武器随死亡清区进弃牌堆。

    与 `test_borrowed_sword_a_dead_user_does_not_receive_the_weapon_when_the_holder_passes`
    的**结果不同**：那里持刀者答 `Pass` ⇒ 派生"交武器"（被授予口挡住后留在原处）；这里
    持刀者已死 ⇒ 取消，使用者拿不到任何东西。
    """
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="borrowed_sword",
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(slash,), equip=(weapon,)),
            SeatSpec(),
        ],
        deck=_deck_excluding(pack, borrowed, weapon, slash),
        turn_seat=0,
        respond="use",
        use_target=2,
        designations=(2,),
        die_at=_DeathAt(
            on_seat=0, victim=1, kind=QueryKind.RESPOND, cause=WindowCause.NULLIFY
        ),
    )

    assert _windows(engine, WindowCause.BORROWED_SWORD) == [], (
        "持刀者已死 ⇒ 不向他发问（取消，不是 `Pass`：`Pass` 会派生出『交武器』）"
    )
    assert engine.state.player(1).equip == [], "装备区随死亡清空"
    assert weapon in engine.state.discard, "武器随死亡清区进弃牌堆"
    assert weapon not in engine.state.player(0).hand, "使用者拿不到"
    assert engine.state.player(2).hp == 4, "没有派生【杀】"
    assert borrowed in engine.state.discard


def test_borrowed_sword_a_dead_holder_is_cancelled_not_handed_over(pack):
    """借刀：持刀者已死 ⇒ 询问取消，auto-b 不得把武器交给使用者（票 01 的交待）。

    直调回调：引擎的目标存活闸会先把"目标（持刀者）已死"的整段跳过，故"持刀者已死、武器
    还在装备区"这一中间态只在 op 的缝上可判。`auto_skip` 的"没有候选 ⇒ 直接 b"读的正是
    这份预览：不先判死亡，`_hand_over_weapon` 走的"从死者取走"不被授予口挡，武器会交给
    存活的使用者。判据只读 `state.player(holder).alive`，不改引擎闸。
    """
    from sgs.effects.ops import resolve_borrowed_sword

    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(alive=False, hp=0, equip=(weapon,)),
            SeatSpec(),
        ],
        deck=_deck_excluding(pack, borrowed, weapon),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    frame = engine.push_frame(
        FrameKind.RESOLVE,
        {
            "card": state.card(borrowed),
            "source": 0,
            "targets": (1,),
            "cursor": 0,
            "designations": (2,),
        },
        timing=Timing.RESOLVE_START,
    )

    result = resolve_borrowed_sword(engine, frame)

    assert engine.cancelled(result), "持刀者已阵亡 ⇒ 询问取消（不是 `Pass` / 空选）"
    assert weapon in state.player(1).equip, (
        "『否则交武器』不派生：auto-b 不得把武器从死者装备区交给使用者"
    )
    assert weapon not in state.player(0).hand
    assert _queries(engine, QueryKind.RESPOND) == [], (
        "窗口外不向死者发问，也不落询问记录（取消由引擎闸返回，不进 QueryRecord）"
    )


def test_duel_a_dead_user_still_lets_the_target_respond(pack):
    """决斗：使用者死时轮到目标响应 ⇒ 目标照常收到窗口（不向死者发问）。"""
    duel = cards_of_type(pack, "duel")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="duel",
        seats=[SeatSpec(hand=(duel,)), SeatSpec(hand=(slash,)), SeatSpec()],
        deck=_deck_excluding(pack, duel, slash),
        turn_seat=0,
        respond="pass",
        die_at=_DeathAt(
            on_seat=1, victim=0, kind=QueryKind.RESPOND, cause=WindowCause.DUEL
        ),
    )

    windows = _windows(engine, WindowCause.DUEL)
    assert [query.actor for query in windows] == [1], "轮到的响应者是目标，照常被问"
    assert any(isinstance(action, PlayCard) for action in windows[0].legal_actions), (
        "目标手里那张【杀】仍在候选面里"
    )


def test_duel_a_dead_user_still_lets_the_target_take_sourceless_damage(pack):
    """决斗：目标不出杀 ⇒ 目标受 1 点（来源已阵亡 ⇒ 无来源）。"""
    duel = cards_of_type(pack, "duel")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="duel",
        seats=[SeatSpec(hand=(duel,)), SeatSpec(hand=(slash,)), SeatSpec()],
        deck=_deck_excluding(pack, duel, slash),
        turn_seat=0,
        respond="pass",
        die_at=_DeathAt(
            on_seat=1, victim=0, kind=QueryKind.RESPOND, cause=WindowCause.DUEL
        ),
    )

    assert engine.state.player(1).hp == 3, "目标不出【杀】⇒ 受 1 点"
    assert engine.damage_events[-1].target == 1
    assert engine.damage_events[-1].source is None, "来源已阵亡 ⇒ 无来源伤害"
    assert engine.damage_events[-1].amount == 1
    deals = _timings(engine, Timing.DAMAGE_DEAL)
    assert deals and deals[-1].actor is None, "DAMAGE 时机的 actor 也不认死者"
    assert not [delta for delta in _deltas(engine, "hp_change") if delta.seat == 0], (
        "死者不落体力"
    )


def test_duel_a_dead_user_is_cancelled_when_the_turn_returns_to_them(pack):
    """决斗：目标出杀 ⇒ 轮到使用者（已死），询问取消 ⇒ 无人受伤（取消 ≠ `Pass`）。"""
    duel = cards_of_type(pack, "duel")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="duel",
        seats=[SeatSpec(hand=(duel,)), SeatSpec(hand=(slash,)), SeatSpec()],
        deck=_deck_excluding(pack, duel, slash),
        turn_seat=0,
        respond="play",
        die_at=_DeathAt(
            on_seat=1, victim=0, kind=QueryKind.RESPOND, cause=WindowCause.DUEL
        ),
        ask_policy=AskPolicy(auto_skip=True),
    )

    assert [query.actor for query in _windows(engine, WindowCause.DUEL)] == [1], (
        "轮回到已死使用者 ⇒ 不再发问（`Pass` 会判他受伤；取消不会）"
    )
    assert [player.hp for player in engine.state.players] == [4, 4, 4], (
        "取消不是 `Pass` ⇒ 无人受伤（目标的【杀】是响应打出，不造成伤害）"
    )
    assert not _timings(engine, Timing.DAMAGE_DEAL), "没有压出伤害帧"
    assert engine.damage_events == ()


def test_duel_a_dead_user_whose_turn_it_is_is_not_asked(pack):
    """决斗：使用者死时轮到自己 ⇒ 询问取消 ⇒ 无人受伤。

    中间态：`push_frame` 摆一个"目标表已冻结"的 RESOLVE 帧，帧私有 `op.duel_turn` 直接
    指向已死的使用者；`AskPolicy.auto_skip` 打开（网页缺省口径）——预筛分支不得把"没有
    候选"读成 `Pass` 而把伤害压向死者。
    """
    duel = cards_of_type(pack, "duel")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(alive=False, hp=0), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, duel),
        processing=(duel,),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=1, ask_policy=AskPolicy(auto_skip=True))
    engine.push_frame(
        FrameKind.RESOLVE,
        {
            "card": state.card(duel),
            "source": 0,
            "targets": (1,),
            "cursor": 0,
            "op": {"duel_turn": 0},
        },
        timing=Timing.RESOLVE_START,
    )
    _run_constructed(engine, 3)

    assert _queries(engine, QueryKind.RESPOND) == [], (
        "轮到的响应者已死 ⇒ 不发问（`auto_skip` 的预筛不得绕过死亡判据）"
    )
    assert [player.hp for player in engine.state.players] == [0, 4, 4], "取消 ⇒ 无人受伤"
    assert not _timings(engine, Timing.DAMAGE_DEAL), "没有压出伤害帧"
    assert engine.damage_events == ()


def test_fire_attack_a_dead_user_after_the_reveal_cancels_the_discard_and_deals_no_damage():
    """火攻：使用者死在目标亮牌后 ⇒ 弃同花色牌的询问取消 ⇒ 无伤害。

    亮牌只是公开信息（目标照常亮），取消的是使用者的弃牌询问——`_queries` 里没有 actor 0
    的 SELECT_CARDS（`Pass` 会留下一条询问 + 一条已接受动作，取消一条都没有）。
    """
    military = load_content_pack(deck_profile="standard_ex_160")
    fire = cards_of_type(military, "fire_attack")[0]
    peach = cards_of_type(military, "peach")[0]
    engine, _ = _run_trick(
        military,
        play="fire_attack",
        seats=[SeatSpec(hand=(fire,)), SeatSpec(hand=(peach,)), SeatSpec()],
        deck=_deck_excluding(military, fire, peach),
        turn_seat=0,
        play_target=1,
        die_at=_DeathAt(on_seat=1, victim=0, kind=QueryKind.SELECT_CARDS),
    )

    reveals = [delta for delta in _deltas(engine, "card_reveal")]
    assert len(reveals) == 1 and reveals[0].seat == 1, "目标照常亮牌（公开信息）"
    selections = _queries(engine, QueryKind.SELECT_CARDS)
    assert [query.actor for query in selections] == [1], (
        "死者的弃同花色询问取消（不是 `Pass`：那一问根本没有 QueryRecord）"
    )
    assert [player.hp for player in engine.state.players] == [4, 4, 4], "无伤害"
    assert not _timings(engine, Timing.DAMAGE_DEAL), "火攻的伤害帧不压"
    assert engine.damage_events == ()
    assert fire in engine.state.discard, "使用牌照常进弃牌堆"


def test_barbarian_invasion_a_dead_user_still_lets_the_others_respond_and_takes_sourceless_damage(pack):
    """南蛮：使用者死 ⇒ 其余目标照常响应；失败者受伤害（无来源）。"""
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="barbarian_invasion",
        seats=[SeatSpec(hand=(barbarian,)), SeatSpec(hand=(slash,)), SeatSpec()],
        deck=_deck_excluding(pack, barbarian, slash),
        turn_seat=0,
        respond="play",
        die_during_nullify=True,
    )

    assert [query.actor for query in _windows(engine, WindowCause.BARBARIAN_INVASION)] == [
        1,
        2,
    ], "其余目标照常逐座响应"
    assert engine.state.player(1).hp == 4, "打出【杀】者免伤"
    assert engine.state.player(2).hp == 3, "失败者受到 1 点伤害"
    assert engine.damage_events[-1].source is None, "来源已阵亡 ⇒ 无来源"
    deals = _timings(engine, Timing.DAMAGE_DEAL)
    assert deals and deals[-1].actor is None
    assert slash in engine.state.discard, "打出的【杀】照常进弃牌堆"


def test_arrow_barrage_a_dead_user_still_lets_the_others_respond_and_takes_sourceless_damage(pack):
    """万箭：使用者死 ⇒ 其余目标照常响应；失败者受伤害（无来源）。"""
    arrows = cards_of_type(pack, "arrow_barrage")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    engine, _ = _run_trick(
        pack,
        play="arrow_barrage",
        seats=[SeatSpec(hand=(arrows,)), SeatSpec(hand=(dodge,)), SeatSpec()],
        deck=_deck_excluding(pack, arrows, dodge),
        turn_seat=0,
        respond="play",
        die_during_nullify=True,
    )

    assert [query.actor for query in _windows(engine, WindowCause.ARROW_BARRAGE)] == [1, 2], (
        "其余目标照常逐座响应"
    )
    assert engine.state.player(1).hp == 4, "打出【闪】者免伤"
    assert engine.state.player(2).hp == 3, "失败者受到 1 点伤害"
    assert engine.damage_events[-1].source is None, "来源已阵亡 ⇒ 无来源"
    deals = _timings(engine, Timing.DAMAGE_DEAL)
    assert deals and deals[-1].actor is None


def test_a_slash_user_who_dies_in_the_dodge_window_still_deals_sourceless_damage(pack):
    """杀：使用者死于【闪】窗口 ⇒ 目标照常被问【闪】；不出闪 ⇒ 伤害落（无来源）。"""
    slash = cards_of_type(pack, "slash")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    engine, _ = _run_trick(
        pack,
        play="slash",
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(dodge,)), SeatSpec()],
        deck=_deck_excluding(pack, slash, dodge),
        turn_seat=0,
        respond="pass",
        die_at=_DeathAt(on_seat=1, victim=0, kind=QueryKind.DODGE),
    )

    assert [query.actor for query in _queries(engine, QueryKind.DODGE)] == [1], (
        "目标照常被问【闪】"
    )
    assert engine.state.player(1).hp == 3, "不出闪 ⇒ 伤害落"
    assert engine.damage_events[-1].target == 1
    assert engine.damage_events[-1].source is None, "使用者已阵亡 ⇒ 无来源伤害"
    deals = _timings(engine, Timing.DAMAGE_DEAL)
    assert deals and deals[-1].actor is None
    assert slash in engine.state.discard


def test_a_slash_user_who_dies_in_the_dodge_window_stops_when_the_target_dodges(pack):
    """杀：目标出闪 ⇒ 该目标结算终止（无伤害），使用者阵亡不额外派生任何段。"""
    slash = cards_of_type(pack, "slash")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    engine, _ = _run_trick(
        pack,
        play="slash",
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(dodge,)), SeatSpec()],
        deck=_deck_excluding(pack, slash, dodge),
        turn_seat=0,
        respond="use",
        die_at=_DeathAt(on_seat=1, victim=0, kind=QueryKind.DODGE),
    )

    assert [query.actor for query in _queries(engine, QueryKind.DODGE)] == [1]
    assert engine.state.player(1).hp == 4, "出闪 ⇒ 抵消，无人受伤"
    assert not _timings(engine, Timing.DAMAGE_DEAL), "没有压出伤害帧"
    assert engine.damage_events == ()
    assert dodge in engine.state.discard, "打出的【闪】照常进弃牌堆"
    assert slash in engine.state.discard


def test_peach_garden_a_dead_user_heals_the_wounded_living_but_not_the_dead(pack):
    """桃园：使用者死 ⇒ 已受伤的存活角色各回复 1；已受伤的死者不回复。"""
    garden = cards_of_type(pack, "peach_garden")[0]
    engine, _ = _run_trick(
        pack,
        play="peach_garden",
        seats=[SeatSpec(hp=2, hand=(garden,)), SeatSpec(hp=2), SeatSpec(hp=4)],
        deck=_deck_excluding(pack, garden),
        turn_seat=0,
        die_during_nullify=True,
    )

    assert [player.hp for player in engine.state.players] == [2, 3, 4], (
        "死者（已受伤）不回复；已受伤的存活者各回复 1；未受伤者照旧"
    )
    assert engine.state.player(0).alive is False
    assert [record.targets[0] for record in _timings(engine, Timing.RECOVER_VALUE)] == [1], (
        "回复段只对存活目标压帧"
    )
    assert garden in engine.state.discard, "使用牌照常进弃牌堆"
