"""一将成名-2014：沮授【渐营】/【矢北】（jushou-lusu 票 01）。

票面 = ``.scratch/jushou-lusu/issues/01-jushou.md``（用户 2026-11-08 签「询问剧本」+ Q1–Q5）。
牌面权威 = 用户 ``技能.txt`` 逐字，与 OL 一将成名官网同值（票面 §0.1）。

    【渐营】当你于你的出牌阶段内使用牌时，若此牌与你本阶段使用的上一张牌点数或花色
            相同，你可以摸一张牌。
    【矢北】锁定技，当你受到伤害后，若此次伤害是你本回合受到的第一次伤害，则你回复1点体力，
            否则你失去1点体力。

两条技能的**跨事件记忆都住在引擎侧座位事实**（用户签的 Q1 / Q2）：

- 【渐营】``Player.play_phase_last_card`` / ``play_phase_previous_card``，由
  ``Engine._note_play_phase_card`` 在 ``start_use`` 入口**旋转**（打出不写、别人的回合不写），
  出牌阶段起点复位；
- 【矢北】``Player.damage_taken_this_turn``，由 ``note_damage_taken`` 在
  ``damage_finalize_amount`` 里**每个伤害事件 +1**，回合末对所有座位清零。

判据全走**外部缝**：记录流里的时机 / 询问、座位上的体力变化、以及手牌 / 牌堆 / 装备区
的终局状态。**不 mock、不旁路候选**（虚拟牌用 ``transformed_card`` 这一条共享构造器造）。
"""

from __future__ import annotations

from sgs.data import Category, Suit, card_category, load_content_pack
from sgs.engine import Engine
from sgs.engine.steps import damage_finalize_amount
from sgs.resolution import Tag, add_tag
from sgs.skills.virtual import transformed_card
from sgs.state import (
    ActivateSkill,
    CardRef,
    EndPhase,
    FrameKind,
    Pass,
    Phase,
    QueryKind,
    SkillSuppression,
    UseCard,
)
from sgs.timing import Timing

from support import (
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
)

PACK = load_content_pack(deck_profile="standard_108")

HERO = "jushou"
JIANYING = "hero.jushou.jianying"
SHIBEI = "hero.jushou.shibei"
QINGJIAN = "hero.jie_xiahoudun.qingjian"


# ---------------------------------------------------------------------------
# 牌面取样：按 (花色 / 点数) 从标准 108 张里挑**使用无需目标**的牌
# ---------------------------------------------------------------------------


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_card(card_id: int):
    return PACK.deck.cards[card_id]


def _is_equipment(card) -> bool:
    return card_category(PACK.pack_id, card.type) is Category.EQUIPMENT


def _equip_with_suit(suit: Suit, *, skip: int | None = None) -> int:
    """一张**装备牌**里花色 = ``suit`` 的牌（装备牌的使用不需要指定目标）。"""
    for card_id, card in enumerate(PACK.deck.cards):
        if _is_equipment(card) and card.suit is suit and card_id != skip:
            return card_id
    raise AssertionError(f"标准 108 里没有花色 {suit} 的装备牌")


def _equip_with_rank(rank: int, *, skip_suit: Suit | None = None) -> int:
    for card_id, card in enumerate(PACK.deck.cards):
        if _is_equipment(card) and card.rank == rank:
            if skip_suit is None or card.suit is not skip_suit:
                return card_id
    raise AssertionError(f"标准 108 里没有点数 {rank} 的装备牌")


# 同花色（不同点数）／同点数（不同花色）／两者都不同 的三对装备牌。
SAME_SUIT_A = _equip_with_suit(Suit.HEART)
SAME_SUIT_B = _equip_with_suit(Suit.HEART, skip=SAME_SUIT_A)
_RANK = _deck_card(SAME_SUIT_A).rank
SAME_RANK_B = _equip_with_rank(_RANK, skip_suit=Suit.HEART)


def _other_both() -> int:
    """与 ``SAME_SUIT_A`` **花色与点数都不同**的装备牌。"""
    a = _deck_card(SAME_SUIT_A)
    for card_id, card in enumerate(PACK.deck.cards):
        if _is_equipment(card) and card.suit is not a.suit and card.rank != a.rank:
            return card_id
    raise AssertionError("标准 108 里没有既不同花色也不同点数的装备牌")


BOTH_DIFFERENT = _other_both()


# ---------------------------------------------------------------------------
# 推进器
# ---------------------------------------------------------------------------


def _answer(query, *, activate: tuple[str, ...] = (), use_peach: bool = False):
    """最小的合法应答：出牌阶段结束；指定技能答「发动」；其余一律放弃。

    ``use_peach`` 为真时，濒死求桃窗口里会用掉手里第一张【桃】（"被救活后矢北才结算"
    那一条断言要靠它）。
    """
    if query.kind == QueryKind.PLAY_PHASE:
        return next(action for action in query.legal_actions if isinstance(action, EndPhase))
    if query.kind == QueryKind.SKILL_ACTIVATION and str(query.skill_id) in activate:
        for action in query.legal_actions:
            if isinstance(action, ActivateSkill):
                return action
    if use_peach and query.kind == QueryKind.PEACH:
        for action in query.legal_actions:
            if (
                isinstance(action, UseCard)
                and card_category(PACK.pack_id, action.card.type) is Category.BASIC
                and action.card.type == "peach"
            ):
                return action
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def _drive(engine, stop, *, activate=(), use_peach=False, limit=900):
    for _ in range(limit):
        if stop(engine):
            return engine
        if engine.finished:
            return engine
        query = engine.pending
        if query is None:
            engine._step()
            continue
        engine.submit(_answer(query, activate=activate, use_peach=use_peach))
    raise AssertionError("推进上限内没有到达停止条件")


def _is_play_idle(engine) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
        and engine.pending.actor == 0
    )


def _asked(engine, skill_id: str) -> list:
    """记录流里该技能的「是否发动」询问（按出现顺序）。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and str(record.query.skill_id) == skill_id
    ]


def _boot(
    hand,
    *,
    seats: int = 2,
    turn_seat: int = 0,
    hero: str = HERO,
    hp: int = 3,
):
    """构造一个从座位 0 出牌阶段起跑的局面（座位 0 = 沮授）。

    """
    specs = [SeatSpec(hero=hero, hp=hp, hp_max=3, hand=tuple(hand))]
    specs += [SeatSpec() for _ in range(seats - 1)]
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=deck_excluding(PACK, *hand),
        turn_seat=turn_seat,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    return state, engine


def _to_play_idle(engine):
    return _drive(engine, _is_play_idle)


def _use(engine, card_id: int, *, targets: tuple[int, ...] | None = None):
    """在出牌阶段的空闲点提交一次使用（不推进）。

    ``targets=None`` = 取该牌**自己**的那条候选（装备牌自带的 ``(0,)``、多目标【杀】的
    指定目标另行显式给）——不按"空目标"硬匹配，免得把装备牌挡在门外。
    """
    query = engine.pending
    action = next(
        action
        for action in query.legal_actions
        if isinstance(action, UseCard)
        and action.card.card_id == card_id
        and (targets is None or action.targets == tuple(targets))
    )
    engine.submit(action)


def _use_and_settle(engine, card_id: int, *, targets=None, activate=(JIANYING,)):
    """提交一次使用并推进回出牌阶段空闲（途中该答的「是否发动」按 ``activate`` 答）。"""
    _use(engine, card_id, targets=targets)
    _drive(engine, _is_play_idle, activate=activate)
    return engine


def _use_expecting_ask(engine, card_id: int, *, targets=None, skill_id: str = JIANYING):
    """提交一次使用并推进到该技能「是否发动」那一问（停在那里，不答）。"""
    _use(engine, card_id, targets=targets)
    _drive(
        engine,
        lambda e: (
            e.pending is not None
            and e.pending.kind == QueryKind.SKILL_ACTIVATION
            and str(e.pending.skill_id) == skill_id
        ),
    )
    return engine


# ---------------------------------------------------------------------------
# §1 渐营 —— ① 触发：同花色 / 同点数
# ---------------------------------------------------------------------------


def test_jianying_draws_when_the_same_suit_follows():
    """同花色（点数不同）⇒ 问一次、答发动摸一张牌堆顶。"""
    state, engine = _boot([SAME_SUIT_A, SAME_SUIT_B])
    _to_play_idle(engine)
    _use_and_settle(engine, SAME_SUIT_A)
    assert _asked(engine, JIANYING) == [], "本阶段第一张牌没有「上一张」⇒ 连问都不问"

    deck_before = list(state.deck)
    hand_before = list(state.player(0).hand)
    _use_expecting_ask(engine, SAME_SUIT_B)
    assert len(_asked(engine, JIANYING)) == 1, "同花色 ⇒ 问一次「是否发动」"
    before = list(state.player(0).hand)
    _drive(engine, _is_play_idle, activate=(JIANYING,))

    assert state.player(0).hand == [*before, deck_before[0]], "发动后摸牌堆顶那一张"
    assert hand_before == [SAME_SUIT_B, *before], "使用前的手牌序不被动过"


def test_jianying_draws_when_the_same_rank_follows():
    """同点数（花色不同）⇒ 同样触发。"""
    state, engine = _boot([SAME_SUIT_A, SAME_RANK_B])
    _to_play_idle(engine)
    _use_and_settle(engine, SAME_SUIT_A)
    deck_before = list(state.deck)

    _use_expecting_ask(engine, SAME_RANK_B)
    assert len(_asked(engine, JIANYING)) == 1, "同点数 ⇒ 问一次"
    before = list(state.player(0).hand)
    _drive(engine, _is_play_idle, activate=(JIANYING,))

    assert state.player(0).hand == [*before, deck_before[0]]


def test_jianying_declining_draws_nothing():
    """答「不发动」⇒ 一张都不摸（「可以」的放弃出口）。"""
    state, engine = _boot([SAME_SUIT_A, SAME_SUIT_B])
    _to_play_idle(engine)
    _use_and_settle(engine, SAME_SUIT_A)
    _use_expecting_ask(engine, SAME_SUIT_B)
    before = list(state.player(0).hand)
    deck_before = list(state.deck)

    _drive(engine, _is_play_idle)  # 不把 JIANYING 放进 activate ⇒ 答「放弃」

    assert len(_asked(engine, JIANYING)) == 1
    assert state.player(0).hand == before
    assert list(state.deck) == deck_before


def test_jianying_is_not_offered_when_both_suit_and_rank_differ():
    """花色与点数都不同 ⇒ 不问。"""
    _, engine = _boot([SAME_SUIT_A, BOTH_DIFFERENT])
    _to_play_idle(engine)
    _use_and_settle(engine, SAME_SUIT_A)
    _use_and_settle(engine, BOTH_DIFFERENT)
    assert _asked(engine, JIANYING) == []


def test_jianying_is_not_offered_on_the_first_card_of_the_play_phase():
    """本阶段第一张「使用」之前没有「上一张牌」⇒ 不问（每一回合都重新开始）。"""
    _, engine = _boot([SAME_SUIT_A, SAME_SUIT_B])
    _to_play_idle(engine)
    _use_and_settle(engine, SAME_SUIT_B)
    assert _asked(engine, JIANYING) == []


# ---------------------------------------------------------------------------
# §1 渐营 —— ② 作用域：只认**自己的出牌阶段**与**使用**（不含打出）
# ---------------------------------------------------------------------------


def test_jianying_ignores_a_card_used_outside_my_play_phase():
    """别人的出牌阶段里使用牌 ⇒ 既不记录、也不触发（牌面写「你的出牌阶段内」）。"""
    _, engine = _boot([SAME_SUIT_A, SAME_SUIT_B], turn_seat=1)
    # 直接压一次"座位 0 在座位 1 的回合里使用"（构造局面：绕过出牌阶段的候选面）。
    engine.start_use(engine.state.card(SAME_SUIT_A), source=0, targets=())
    _drive(engine, lambda e: _is_play_idle(e) or e.finished)
    player = engine.state.player(0)
    assert player.play_phase_last_card is None, "不是自己的出牌阶段 ⇒ 不写座位事实"

    engine.start_use(engine.state.card(SAME_SUIT_B), source=0, targets=())
    _drive(engine, lambda e: _is_play_idle(e) or e.finished)
    assert _asked(engine, JIANYING) == []


def test_jianying_does_not_record_a_played_card():
    """**打出**不是使用：``play_card`` 不写"上一张使用牌"（牌面写"使用"）。"""
    state, engine = _boot([SAME_SUIT_A, SAME_SUIT_B])
    engine.play_card(state.card(SAME_SUIT_A), source=0, timing=Timing.PLAY_ON_PLAY)
    assert state.player(0).play_phase_last_card is None
    assert state.player(0).play_phase_previous_card is None


# ---------------------------------------------------------------------------
# §1 渐营 —— ③ 转化 / 虚拟牌的点数与花色（fail-closed）
# ---------------------------------------------------------------------------


def test_jianying_comparison_is_fail_closed_without_rank_or_suit():
    """「点数或花色相同」的唯一判据：任一侧缺该项 ⇒ 那一项**不算相同**（账本 L896）。

    虚拟牌的两种形态由共享构造器给（:func:`~sgs.skills.virtual.transformed_card`）：单源
    继承来源牌的两项、多源 / 纯虚拟恒无。**判据只住这一处**
    （``use_after._same_rank_or_suit``），故这里直接对纯函数断言——引擎侧的"上一张牌"是
    普通实体牌，虚拟牌进不进得去候选面由转化技的清单入口决定，与本技能的判据无关。
    """
    from sgs.skills.use_after import _same_rank_or_suit

    heart_five = CardRef(
        card_id=0, type="slash", name="杀", suit=Suit.HEART, rank=5
    )
    heart_nine = CardRef(
        card_id=1, type="dodge", name="闪", suit=Suit.HEART, rank=9
    )
    spade_five = CardRef(
        card_id=2, type="peach", name="桃", suit=Suit.SPADE, rank=5
    )
    rankless = CardRef(card_id=3, type="slash", name="杀", suit=None, rank=None)

    assert _same_rank_or_suit(heart_nine, heart_five) is True, "同花色"
    assert _same_rank_or_suit(spade_five, heart_five) is True, "同点数"
    assert _same_rank_or_suit(rankless, heart_five) is False, "无点数花色 ⇒ 不成立"
    assert _same_rank_or_suit(heart_five, rankless) is False, "上一张无点数花色 ⇒ 不成立"
    assert _same_rank_or_suit(rankless, rankless) is False, "两边都缺 ≠ 相同"
    assert _same_rank_or_suit(None, heart_five) is False, "没有上一张牌"


# ---------------------------------------------------------------------------
# §1 渐营 —— ④ 触发粒度 / 逐事件
# ---------------------------------------------------------------------------


def test_jianying_asks_once_for_a_multi_target_card():
    """一张**多目标**锦囊是**一次使用** ⇒ 只问一次、只摸一张（账本 L777 逐事件）。"""
    barrage = _cards("arrow_barrage")[0]  # ♥1：【万箭齐发】指定所有其他角色
    state, engine = _boot([SAME_SUIT_A, barrage], seats=3)
    _to_play_idle(engine)
    _use_and_settle(engine, SAME_SUIT_A)
    deck_before = list(state.deck)

    _use_expecting_ask(engine, barrage, targets=(1, 2))
    before = list(state.player(0).hand)
    _drive(engine, _is_play_idle, activate=(JIANYING,))

    assert len(_asked(engine, JIANYING)) == 1, "多目标也只是一次使用"
    assert state.player(0).hand == [*before, deck_before[0]], "只摸一张"


# ---------------------------------------------------------------------------
# §1 渐营 —— ⑤ 挂起续跑 / 复位点
# ---------------------------------------------------------------------------


def test_jianying_draws_once_when_a_gain_hook_suspends():
    """摸牌内联触发别人的【清俭】并挂起 ⇒ 续跑后**不重摸**（帧私有游标）。"""
    state, engine = _boot([SAME_SUIT_A, SAME_SUIT_B])
    state.player(0).granted_skills.append(QINGJIAN)
    _to_play_idle(engine)
    _use_and_settle(engine, SAME_SUIT_A)
    _use_expecting_ask(engine, SAME_SUIT_B)
    hand_size = len(state.player(0).hand)
    deck_before = list(state.deck)

    # 渐营发动（摸牌）→ 清俭的「是否发动」挂起 → 答放弃 → 续跑。
    _drive(engine, _is_play_idle, activate=(JIANYING,))
    assert len(state.player(0).hand) == hand_size + 1, "只摸一张（重入不重摸）"
    assert list(state.deck) == deck_before[1:]


def test_jianying_resets_the_last_card_at_the_play_phase_start():
    """复位点 = 出牌阶段起点：新阶段的"上一张牌"从空开始（乐不思蜀跳过时同一条路径）。"""
    state, engine = _boot([SAME_SUIT_A, SAME_SUIT_B])
    _to_play_idle(engine)
    _use_and_settle(engine, SAME_SUIT_A)
    assert state.player(0).play_phase_last_card is not None

    engine._phase_play_start()  # noqa: SLF001 - 与既有"复位点"断言同一手法

    assert state.player(0).play_phase_last_card is None
    assert state.player(0).play_phase_previous_card is None


# ---------------------------------------------------------------------------
# §2 矢北 —— ① 第一 / 后续伤害两档
# ---------------------------------------------------------------------------


def _damage_frame(state, *, amount: int, source: int | None, target: int, card=None):
    channel = card if card is not None else None
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


def _damage_end_count(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_END
    )


def _hit(engine, *, amount=1, source=1, target=0, card=None):
    """``card`` 是**渠道牌**（``CardRef | None``；虚拟牌也收）。"""
    """压一个 ``DAMAGE`` 子帧并推进到它结束（同一次伤害事件）。"""
    payload = _damage_frame(
        engine.state, amount=amount, source=source, target=target, card=card
    )
    expected = _damage_end_count(engine) + 1
    engine.push_frame(FrameKind.DAMAGE, payload, timing=Timing.DAMAGE_DEAL)
    _drive(engine, lambda e: _damage_end_count(e) >= expected, use_peach=True)
    return engine


def _hurt_seats(*, hp: int = 3):
    """座位 0 = 沮授（3 体力），座位 1 = 伤害来源。"""
    return [SeatSpec(hero=HERO, hp=hp, hp_max=3), SeatSpec()]


def _hurt_boot(*, hp: int = 3, hand=(), turn_seat: int = 0):
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hp=hp, hp_max=3, hand=tuple(hand)), SeatSpec()],
        deck=deck_excluding(PACK, *hand),
        turn_seat=turn_seat,
        phase=Phase.PLAY,
    )
    return state, Engine.from_state(state, seed=7)


def test_shibei_first_damage_heals_one():
    """本回合第一次受伤 ⇒ 回复 1 点（净变化 = 0）。"""
    state, engine = _hurt_boot(hp=3)
    _hit(engine, amount=1)
    assert state.player(0).hp == 3
    assert state.player(0).damage_taken_this_turn == 1
    assert any(
        record.kind == "timing" and record.timing is Timing.RECOVER_VALUE
        for record in engine.records
    ), "回复走 RECOVER 帧（别人的改回复量技能看得到）"


def test_shibei_second_damage_loses_one():
    """本回合第二次受伤 ⇒ 失去 1 点体力（3 → 2 → 1，再失 1 ⇒ 0 起濒死结算）。"""
    state, engine = _hurt_boot(hp=3)
    _hit(engine, amount=1)  # 第一次：回复 ⇒ 仍是 3
    assert state.player(0).hp == 3
    _hit(engine, amount=1)  # 第二次：先扣 1（⇒2），再失去 1（⇒1）
    assert state.player(0).hp == 1
    assert state.player(0).damage_taken_this_turn == 2


def test_shibei_two_point_first_damage_heals_only_one():
    """一次 2 点伤害仍是**一个**伤害事件：只算 1 次、只回复 1 点。"""
    state, engine = _hurt_boot(hp=3)
    _hit(engine, amount=2)
    assert state.player(0).hp == 2, "3 - 2 + 1 = 2"
    assert state.player(0).damage_taken_this_turn == 1


def test_shibei_counts_every_damage_event_not_each_point():
    """2 点伤害算 1 次；紧随其后的 1 点才算第 2 次（⇒ 失去 1 点）。"""
    state, engine = _hurt_boot(hp=3)
    _hit(engine, amount=2)
    assert state.player(0).damage_taken_this_turn == 1
    _hit(engine, amount=1)
    assert state.player(0).damage_taken_this_turn == 2
    assert state.player(0).hp == 0 or not state.player(0).alive, "第二次 ⇒ 失去体力，进濒死"


# ---------------------------------------------------------------------------
# §2 矢北 —— ② "本回合"的作用域：含别人的回合、回合末清零
# ---------------------------------------------------------------------------


def test_shibei_counts_damage_taken_in_another_players_turn():
    """别人的回合里挨打同样占"本回合"的那一次（一手：官网「一名角色的回合内」）。"""
    state, engine = _hurt_boot(hp=3, turn_seat=1)
    _hit(engine, amount=1)
    assert state.player(0).damage_taken_this_turn == 1
    assert state.player(0).hp == 3, "第一次 ⇒ 回复"


def test_shibei_counter_resets_at_turn_end():
    """复位点 = 回合末（对所有座位清），**不是**自己的 TURN_START。"""
    state, engine = _hurt_boot(hp=3)
    state.player(0).damage_taken_this_turn = 3
    engine._clear_turn_effects()  # noqa: SLF001 - 复位点的直接断言
    assert state.player(0).damage_taken_this_turn == 0


# ---------------------------------------------------------------------------
# §2 矢北 —— ③ 不算"受到伤害"的两种：被防止 / 0 伤害
# ---------------------------------------------------------------------------


def test_shibei_ignores_a_prevented_damage():
    """被防止的伤害不算受到伤害（规则 §4.14 L396–L397）⇒ 不计数、不回复。"""
    state, engine = _hurt_boot(hp=3)
    payload = _damage_frame(state, amount=1, source=1, target=0)
    engine.push_frame(FrameKind.DAMAGE, payload, timing=Timing.DAMAGE_DEAL)
    frame = engine.stack.top
    add_tag(frame, Tag.PREVENTED)
    expected = _damage_end_count(engine) + 1
    _drive(engine, lambda e: _damage_end_count(e) >= expected, use_peach=True)

    assert state.player(0).damage_taken_this_turn == 0
    assert state.player(0).hp == 3


def test_shibei_ignores_a_zero_damage_event():
    """伤害数值为 0 ⇒ 不派发「受到伤害后」⇒ 不计数（同一条判据的另一半）。"""
    state, engine = _hurt_boot(hp=3)
    _hit(engine, amount=0)
    assert state.player(0).damage_taken_this_turn == 0
    assert state.player(0).hp == 3


# ---------------------------------------------------------------------------
# §2 矢北 —— ④ 濒死 / 死亡夹在中间（规则 L498：濒死优先于"受到伤害后"）
# ---------------------------------------------------------------------------


def test_shibei_resolves_after_the_near_death_window():
    """扣到 0 点体力先进濒死；被【桃】救回（1 点）后矢北才结算 ⇒ 回到 2 点。"""
    peach = _cards("peach")[0]
    state, engine = _hurt_boot(hp=1, hand=(peach,))
    _hit(engine, amount=1)
    assert state.player(0).alive, "求桃窗口被救回"
    assert state.player(0).hp == 2, "救回到 1 后，第一次伤害的矢北再回复 1 点"
    assert ran_timing(engine, Timing.DAMAGE_AFTER_TAKE), "伤害后时机确实派发过"


def test_shibei_is_silent_when_this_damage_kills_the_owner():
    """这笔伤害直接把持有者打死 ⇒ 实例不再产出、矢北静默（不再补回复）。"""
    state, engine = _hurt_boot(hp=1)
    _hit(engine, amount=1)
    assert not state.player(0).alive
    assert state.player(0).hp == 0


def test_shibei_second_damage_can_put_the_owner_into_near_death():
    """第二次伤害的"失去 1 点体力"同样会把人打进濒死（无来源、走 LIFE_DECREASE 帧）。"""
    peach = _cards("peach")[0]
    state, engine = _hurt_boot(hp=2, hand=(peach,))
    _hit(engine, amount=1)  # 第一次：回复 ⇒ 2
    assert state.player(0).hp == 2
    _hit(engine, amount=1)  # 第二次：扣 1（⇒1）再失去 1（⇒0）⇒ 濒死 ⇒ 桃救回
    assert state.player(0).alive
    assert state.player(0).hp == 1


# ---------------------------------------------------------------------------
# §2 矢北 —— ⑤ 渠道牌 / 失效语义
# ---------------------------------------------------------------------------


def test_shibei_triggers_for_a_virtual_card_damage():
    """判据不看渠道牌：转化 / 虚拟牌照常触发（这里用单源转化的虚拟【杀】）。"""
    source = _cards("dodge")[0]
    state, engine = _hurt_boot(hp=3)
    virtual = transformed_card(state, (source,), "slash")
    _hit(engine, amount=1, card=virtual)
    assert state.player(0).damage_taken_this_turn == 1
    assert state.player(0).hp == 3


def test_shibei_is_not_suppressed_by_a_non_locked_only_effect():
    """「所有非锁定技失效」不覆盖锁定技（models.py 的 ALL_NON_LOCKED 逐字）⇒ 照常触发。"""
    state, engine = _hurt_boot(hp=3)
    state.player(0).suppressed_skills.append(
        SkillSuppression(skill_id="hero.jie_guanyu.yijue")
    )
    _hit(engine, amount=1)
    assert state.player(0).damage_taken_this_turn == 1
    assert state.player(0).hp == 3


# ---------------------------------------------------------------------------
# 结构面：两条技能都在 CATALOG 里、且锁定位与形状对得上
# ---------------------------------------------------------------------------


def test_the_two_skills_are_declared_with_the_signed_shape():
    """渐营 = 非锁定 + 零步；矢北 = 锁定 + 无流水线（票面 §1.4 / §2.4 的签字形状）。"""
    from sgs.skills import CATALOG, flow_for

    jianying = CATALOG[JIANYING]
    shibei = CATALOG[SHIBEI]

    assert jianying.locked is False
    assert flow_for(JIANYING) is not None and flow_for(JIANYING).steps == ()
    assert shibei.locked is True
    assert flow_for(SHIBEI) is None, "锁定技不问，不必造流水线"
    assert [hook.timing for hook in shibei.hooks] == [Timing.DAMAGE_AFTER_TAKE]
    assert [hook.per_point for hook in shibei.hooks] == [False], "逐事件，不是逐点"
