"""身份局的胜负、同时死亡批次与死亡奖惩（票 25 / 决策回票 14）。

落点 = 库门面（主缝）+ 一条纯函数直测：

1. **纯函数** ``solve_identity_outcome``：只读"全座位身份 + 批次死亡事实 + 批次末存活集合"，
   故用构造事实表直接判决三条分支（主公锚点 / 内奸条件 / 主忠方全灭）；
2. **主缝** ``Engine.from_state`` 的人为局面 + ``declare_death``（受控死亡入口，票 14 §9）：
   身份亮明、胜负宣布时点、奖惩、短摸平局覆盖、同批与异批的差别。

M2 没有天然嵌套死亡的路径，故"同批同时死亡"只能用受控入口造：先声明一个死亡，
在它的检测点之后再声明第二个（压栈守卫在胜负判定后只放行 DEATH 帧）。
"""

from __future__ import annotations

from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import DeathFact, Engine, solve_identity_outcome
from sgs.state import FrameKind, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, declare_death

PACK = load_content_pack(deck_profile="standard_108")
SLASH = cards_of_type(PACK, "slash")
PEACH = cards_of_type(PACK, "peach")

LORD = Identity.LORD
LOYALIST = Identity.LOYALIST
REBEL = Identity.REBEL
RENEGADE = Identity.RENEGADE

FOUR = (LORD, LOYALIST, REBEL, RENEGADE)
FIVE = (LORD, LOYALIST, REBEL, REBEL, RENEGADE)


# ---------------------------------------------------------------------------
# 纯函数：胜负求解（票 14 §2）
# ---------------------------------------------------------------------------


def _fact(identity, snapshot):
    return DeathFact(identity=identity, snapshot=tuple(snapshot))


def _solve(identities, facts, alive):
    return solve_identity_outcome(identities=identities, facts=facts, alive=alive)


def test_lord_death_with_the_renegade_as_the_sole_survivor_is_a_renegade_win():
    victory = _solve(FOUR, [_fact(LORD, (3,))], (3,))

    assert victory is not None
    assert (victory.side, victory.winners) == ("renegade", (3,))


def test_lord_death_when_the_sole_renegade_dies_in_the_same_batch_is_a_lord_side_win():
    """票 14 §2：该内奸同批也死 ⇒ 主忠方胜（R:498-499 的结论）。"""
    facts = [_fact(LORD, (3,)), _fact(RENEGADE, ())]

    victory = _solve(FOUR, facts, ())

    assert victory is not None
    assert (victory.side, victory.winners) == ("lord", (0, 1))


def test_lord_death_with_no_one_else_alive_is_a_lord_side_win():
    """主公最后死：R:510 的主忠方条件直接成立（票 14 §2 的裁定）。"""
    victory = _solve(FOUR, [_fact(LORD, ())], ())

    assert victory is not None
    assert (victory.side, victory.winners) == ("lord", (0, 1))


def test_lord_death_with_a_loyalist_still_alive_is_a_rebel_win():
    """主公一死，除主公外只要不是"恰好一名内奸"，反贼即胜（含只剩忠臣）。"""
    victory = _solve(FOUR, [_fact(LORD, (1,))], (1,))

    assert victory is not None
    assert (victory.side, victory.winners) == ("rebel", (2,))


def test_rebel_winners_include_every_rebel_seat_even_the_dead():
    """票 14 §1：``winners`` = 该侧**全部**座位（含阵亡者，按座位升序）。"""
    facts = [_fact(LORD, (1,)), _fact(REBEL, (1, 4))]

    victory = _solve(FIVE, facts, (1, 4))

    assert victory is not None
    assert (victory.side, victory.winners) == ("rebel", (2, 3))


def test_without_a_lord_death_the_lord_side_wins_once_every_rebel_and_renegade_is_dead():
    facts = [_fact(REBEL, (0, 1, 3)), _fact(RENEGADE, (0, 1))]

    victory = _solve(FOUR, facts, (0, 1))

    assert victory is not None
    assert (victory.side, victory.winners) == ("lord", (0, 1))
    assert victory.reason == "rebels_and_renegade_dead"


def test_without_a_lord_death_the_game_continues_while_a_rebel_or_renegade_lives():
    assert _solve(FOUR, [], (0, 2, 3)) is None, "反贼还活着 ⇒ 继续游戏"
    assert _solve(FOUR, [], (0, 1, 3)) is None, "内奸还活着 ⇒ 继续游戏"


# ---------------------------------------------------------------------------
# 主缝：人为局面 + 受控死亡入口
# ---------------------------------------------------------------------------


def _engine(identities, *, hands=None, deck=(), discard=(), dead=(), pack=PACK, seed=1):
    hands = hands or {}
    seats = [
        SeatSpec(
            hp=0 if index in dead else 4,
            hp_max=4,
            hand=tuple(hands.get(index, ())),
            alive=index not in dead,
            identity=identity,
        )
        for index, identity in enumerate(identities)
    ]
    state = build_state(
        pack=pack,
        rule_config=RuleConfig(player_count=len(identities)),
        seats=seats,
        deck=deck,
        discard=discard,
    )
    return Engine.from_state(state, seed=seed)


def _death_pops(engine):
    return [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.DEATH
    ]


def _advance_until(engine, done, *, limit=200):
    for _ in range(limit):
        if done():
            return
        engine._step()
    raise AssertionError("没有在限定步数内到达目标状态")


def _advance_deaths(engine, count):
    """推进到第 ``count`` 个 DEATH 帧出栈为止（不再走进回合环）。"""
    _advance_until(engine, lambda: len(_death_pops(engine)) >= count)


def _frames(engine, *, op):
    return [record for record in engine.records if record.kind == "frame" and record.op == op]


def _timings(engine, *, frame_id=None):
    records = [record for record in engine.records if record.kind == "timing"]
    if frame_id is not None:
        records = [record for record in records if record.frame_id == frame_id]
    return [record.timing.value for record in records]


def _death_frame(engine):
    return next(
        record
        for record in _frames(engine, op="push")
        if record.frame_kind is FrameKind.DEATH
    )


def _deltas(engine, delta_type):
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def test_identity_mode_death_dispatches_the_five_segments_and_reveals_the_identity():
    engine = _engine(FOUR)
    declare_death(engine, 2, killer=0)

    _advance_deaths(engine, 1)

    death = _death_frame(engine)
    assert _timings(engine, frame_id=death.frame_id) == [
        Timing.DEATH_BEFORE_START.value,
        Timing.DEATH_REVEAL_IDENTITY_BEFORE.value,
        Timing.DEATH_REVEAL_IDENTITY_AFTER.value,
        Timing.DEATH_ON.value,
        Timing.DEATH_AFTER.value,
    ], "身份局的死亡段是五段（票 25 / 决策回票 14 §4）"

    reveals = _deltas(engine, "identity_reveal")
    assert [(record.seat, record.identity) for record in reveals] == [(2, REBEL)]
    reveal = reveals[0]
    before = next(
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.DEATH_REVEAL_IDENTITY_BEFORE
    )
    after = next(
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.DEATH_REVEAL_IDENTITY_AFTER
    )
    assert before.seq < reveal.seq < after.seq, (
        "亮明发生在亮明时机内部、时机技能之后、胜负检测之前（票 14 §5）"
    )


def test_a_non_lord_dying_on_their_own_turn_emits_turn_end_inside_the_death_frame():
    """票 14 §7：死者 == ``turn_seat`` ⇒ ``TURN_END`` 在 ``DEATH_BEFORE_START`` 发出。"""
    engine = _engine(FOUR)
    engine._start()
    engine.state.turn_seat = 2  # 造"反贼在自己的回合内死亡"的形状
    declare_death(engine, 2)

    _advance_deaths(engine, 1)

    death = _death_frame(engine)
    assert _timings(engine, frame_id=death.frame_id)[:2] == [
        Timing.DEATH_BEFORE_START.value,
        Timing.TURN_END.value,
    ], "回合结束排在死亡结算开始前那一段之后（票 14 §4）"


def test_the_lord_dying_on_their_own_turn_does_not_end_the_turn():
    """官方"死亡结算开始前：若死亡的是主公，则游戏结束"——直接判，不做回合结束。"""
    engine = _engine(FOUR)
    engine._start()  # turn_seat = 主公座位
    declare_death(engine, 0)

    _advance_deaths(engine, 1)

    death = _death_frame(engine)
    assert Timing.TURN_END.value not in _timings(engine, frame_id=death.frame_id)


def test_a_non_lord_dying_on_their_own_turn_skips_the_rest_of_the_turn():
    """票 14 §4 的回合结束分支必须**真的**结束回合（票 32 §2 的 fuzz 不变量发现的回归）。

    阵亡者不再摸牌 / 出牌，该回合只发一条 ``TURN_END``（死亡段内那条），随后
    ``TURN_AFTER_END`` 与轮转；第二条 ``TURN_END`` 不得出现。
    """
    engine = _engine(FOUR, deck=tuple(range(1, 40)))
    engine._start()
    engine.state.turn_seat = 2
    declare_death(engine, 2)
    _advance_deaths(engine, 1)

    _advance_until(engine, lambda: engine.state.turn_seat == 3)

    seat_two = [record for record in engine.records if record.kind == "timing" and record.actor == 2]
    assert [record.timing.value for record in seat_two].count(Timing.TURN_END.value) == 1
    assert [record.timing.value for record in seat_two].count(Timing.TURN_AFTER_END.value) == 1
    assert not [
        record.timing.value
        for record in seat_two
        if record.timing.value.startswith("PHASE_")
    ], "阵亡者不得再走任何阶段（不摸牌、不出牌、不弃牌）"
    gained = [
        delta
        for delta in _deltas(engine, "card_move")
        if delta.to_zone is not None and delta.to_zone.seat == 2
    ]
    assert gained == [], "阵亡者不再获得任何牌"


def test_a_lord_death_ends_the_game_with_the_rebel_side_and_its_winners():
    engine = _engine(FOUR)
    declare_death(engine, 0)

    _advance_deaths(engine, 1)

    tail = engine.records[-1]
    assert engine.finished
    assert tail.kind == "game_end"
    assert (tail.result, tail.winner, tail.side, tail.winners) == (
        "winner",
        None,
        "rebel",
        (2,),
    ), "身份局没有单一获胜座位：side + winners（含阵亡者）"
    assert [(record.seat, record.identity) for record in _deltas(engine, "identity_reveal")] == [
        (0, LORD)
    ], "主公也照发亮明记录，形状统一（票 14 §5）"


def test_the_lord_side_wins_with_dead_members_included_in_the_winners():
    """票 14 §1：主忠方获胜时 ``winners`` 也含已阵亡的同伴。"""
    engine = _engine(FOUR, dead=(1,))
    declare_death(engine, 2)  # 反贼
    declare_death(engine, 3)  # 内奸（同批）
    _advance_deaths(engine, 2)

    tail = engine.records[-1]
    assert engine.state.player(1).alive is False
    assert (tail.side, tail.winners) == ("lord", (0, 1))


def test_killing_a_rebel_draws_three_cards_for_the_killer():
    deck = tuple(range(0, 20))
    engine = _engine(FOUR, deck=deck)
    declare_death(engine, 2, killer=0)

    _advance_deaths(engine, 1)

    assert not engine.finished, "内奸还活着 ⇒ 本批不终局"
    assert engine.state.player(2).alive is False
    assert len(engine.state.player(0).hand) == 3, "杀死反贼者摸 3 张（票 14 §4.2）"
    death = _death_frame(engine)
    draws = [
        record
        for record in _deltas(engine, "card_move")
        if record.reason is MoveReason.DRAW and record.frame_id == death.frame_id
    ]
    assert len(draws) == 1
    assert (draws[0].from_zone, draws[0].to_zone, draws[0].owner) == (
        ZoneRef(Zone.DECK),
        ZoneRef(Zone.HAND, 0),
        0,
    )
    assert [card.card_id for card in draws[0].cards] == list(deck[:3]), "与摸牌同一原语"


def test_the_lord_killing_a_loyalist_discards_all_of_the_lords_own_cards():
    """主公杀死忠臣 ⇒ **主公**弃置所有牌（手牌 + 装备区 + 判定区，票 14 §4.2）。"""
    full_pack = load_content_pack(deck_profile="standard_108")
    weapon = cards_of_type(full_pack, "qinggang_sword")[0]
    indulgence = cards_of_type(full_pack, "indulgence")[0]
    engine = _engine(
        FOUR,
        hands={0: (SLASH[0],), 1: (PEACH[0],)},
        pack=full_pack,
    )
    engine.state.player(0).equip.append(weapon)
    engine.state.player(0).judge.append(indulgence)
    declare_death(engine, 1, killer=0)

    _advance_deaths(engine, 1)

    lord = engine.state.player(0)
    assert (lord.hand, lord.equip, lord.judge) == ([], [], []), "主公自己的三区被清空"
    death = _death_frame(engine)
    penalty = [
        record
        for record in _deltas(engine, "card_move")
        if record.frame_id == death.frame_id
        and record.reason is MoveReason.DISCARD
        and record.owner == 0
    ]
    assert {(record.from_zone.zone, record.from_zone.seat) for record in penalty} == {
        (Zone.HAND, 0),
        (Zone.EQUIP, 0),
        (Zone.JUDGE, 0),
    }, "逐来源区各一条 card_move（票 14 §4.2）"
    assert engine.state.player(1).hand == [], "Ⅰ.a 照旧弃置死者区域里的牌"


def test_a_source_less_death_produces_no_reward():
    """无来源伤害（闪电）⇒ killer 未定格 ⇒ 无奖惩（票 14 §4.1）。"""
    deck = tuple(range(0, 20))
    engine = _engine(FOUR, deck=deck)
    declare_death(engine, 2)

    _advance_deaths(engine, 1)

    assert engine.state.player(0).hand == []
    assert [
        record
        for record in _deltas(engine, "card_move")
        if record.reason is MoveReason.DRAW
    ] == []


def test_a_killer_who_died_in_the_same_batch_produces_no_reward():
    """killer 已阵亡 ⇒ 跳过奖惩（票 14 §4.2 的程序化约定）。

    同批的死亡按 LIFO 处理：后声明的内层死亡先结算，故先把忠臣（killer）声明为外层、
    再把反贼声明为内层——反贼结算时忠臣已经阵亡。
    """
    deck = tuple(range(0, 20))
    engine = _engine(FOUR, deck=deck)
    declare_death(engine, 2, killer=1)
    declare_death(engine, 1)

    _advance_deaths(engine, 2)

    assert engine.state.player(2).alive is False
    assert engine.state.player(1).alive is False
    assert [
        record
        for record in _deltas(engine, "card_move")
        if record.reason is MoveReason.DRAW
    ] == [], "凶手已阵亡 ⇒ 不摸牌（保'阵亡者区域为空'不变量）"


def test_a_short_reward_draw_overrides_the_verdict_with_a_draw():
    """任何短摸（含死亡奖惩）一律平局，且**覆盖**已定胜负（票 14 §3 的用户裁定）。"""
    engine = _engine(FOUR)  # 牌堆与弃牌堆皆空、死者也没有牌 ⇒ 奖惩摸牌短摸
    declare_death(engine, 2, killer=0)

    _advance_deaths(engine, 1)

    tail = engine.records[-1]
    assert engine.finished
    assert (tail.result, tail.side, tail.winners, tail.reason) == (
        "draw",
        None,
        (),
        "both_decks_empty",
    )


def test_simultaneous_deaths_are_announced_only_after_the_last_death_of_the_batch():
    """检测点只置暂定结果；宣布在批次末用批次末存活集合重解（票 14 §2 / §3）。"""
    engine = _engine(FOUR, dead=(1, 2))  # 只留主公与内奸
    declare_death(engine, 0)
    _advance_until(engine, lambda: engine.pending_outcome is not None)

    assert engine.pending_outcome.side == "renegade", "检测点的暂定结果：内奸独自存活"
    assert not engine.finished, "检测点不终止：死亡单元照跑"

    declare_death(engine, 3)  # 内奸同批也死
    _advance_deaths(engine, 2)

    tail = engine.records[-1]
    assert engine.finished
    assert (tail.side, tail.winners, tail.reason) == (
        "lord",
        (0, 1),
        "lord_dead_renegade_died_too",
    ), "批次末重解覆盖暂定结果（票 14 §2）"
    assert tail.seq > _death_pops(engine)[-1].seq, "宣布在同批最后一次 DEATH_AFTER 之后"
    assert tail.seq > max(
        record.seq
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DEATH_AFTER
    )


def test_deaths_in_separate_batches_do_not_share_facts():
    """顺序发生的死亡不同批：第一批不终局时清空事实，第二批才判（票 14 §3）。"""
    engine = _engine(FOUR)
    declare_death(engine, 2)  # 第一批：反贼死，内奸还活着
    _advance_deaths(engine, 1)

    assert not engine.finished, "本批无主公死亡且内奸存活 ⇒ 继续对局"

    declare_death(engine, 3)  # 第二批：内奸死 ⇒ 反贼与内奸全灭
    _advance_deaths(engine, 2)

    tail = engine.records[-1]
    assert engine.finished
    assert (tail.side, tail.winners, tail.reason) == (
        "lord",
        (0, 1),
        "rebels_and_renegade_dead",
    )


def test_the_batch_guard_admits_the_nested_death_frame():
    """压栈守卫只放行 DEATH 帧：同批第二次死亡能压进来（票 14 §3）。"""
    engine = _engine(FOUR, dead=(1, 2))
    declare_death(engine, 0)
    _advance_until(engine, lambda: engine.pending_outcome is not None)

    declare_death(engine, 3)  # 不抛 RuntimeError

    assert any(record.frame_kind is FrameKind.DEATH for record in _frames(engine, op="push"))
