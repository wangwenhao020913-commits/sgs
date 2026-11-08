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

import pytest
from sgs.data import (
    Category,
    RuleConfig,
    Suit,
    card_category,
    color_of_suit,
    load_content_pack,
)
from sgs.engine import Engine, IllegalActionError
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
    SelectOptions,
    SkillSuppression,
    SubstituteJudgeCard,
    UseCard,
    WindowCause,
    Zone,
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


# ===========================================================================
# 一将成名-2014·陈群【定品】（yijiang-2014 票 02）
#
# 牌面（用户 `技能.txt` 逐字）：
#
#     出牌阶段，你可以弃置一张手牌（不能是本回合使用或弃置过的类型），令一名已受伤的
#     角色进行一次判定，若结果为：黑色，该角色摸X张牌（X为其已损失的体力值），且本回合
#     不能再对其发动此技能；红色，你翻面。
#
# 与界陈群移动版【定品】**同短 id、四处实质不同**（用户 2026-11-09 签 Q-C1…Q-C4）：
# 代价只**手牌**、目标限**已受伤**、X = **已损失体力值**（不是 min(体力值, 3)）、
# 分支只有**黑 / 红**两段（红 ⇒ 你翻面，♥ 不退款）。【法恩】C1 摘出 ⇒ 本票只落【定品】。
#
# 判据全走**外部缝**：预发动清单（``query.preactivations``）、记录流里的判定 / 搬牌 /
# 翻面、座位上的体力与手牌数。不断言内部结构，也不 mock。
# ===========================================================================

CHENQUN = "chenqun"
DINGPIN = "hero.chenqun.dingpin"
JIE_DINGPIN = "hero.jie_chenqun_mobile.dingpin"


def _first_of(*, category: str = "", exclude: tuple[int, ...] = ()) -> int:
    for card_id in range(len(PACK.deck.cards)):
        if card_id in exclude:
            continue
        kind = card_category(PACK.pack_id, PACK.deck.cards[card_id].type)
        if kind is None:
            continue
        if not category or kind.value == category:
            return card_id
    raise AssertionError(f"标准 108 里找不到大类 {category or '<任意>'} 的牌")


def _basic_with_suit(suit: Suit, *, exclude: tuple[int, ...] = ()) -> int:
    """除 ``exclude`` 外，挑一张**基本牌**里花色 = ``suit`` 的牌（当判定牌用）。"""
    for card_id in range(len(PACK.deck.cards)):
        if card_id in exclude:
            continue
        card = PACK.deck.cards[card_id]
        if card.suit is suit and _category_of(card_id) == "basic":
            return card_id
    raise AssertionError(f"标准 108 里没有花色 {suit} 的基本牌")


def _category_of(card_id: int) -> str:
    kind = card_category(PACK.pack_id, PACK.deck.cards[card_id].type)
    assert kind is not None
    return kind.value


def _hand_of(*, basic: int = 0, trick: int = 0, equipment: int = 0, skip=()) -> tuple[int, ...]:
    """从牌堆里凑一手"指定大类组合"的牌（用于发到座位 0 手里）。"""
    taken: list[int] = []
    for category, count in (("basic", basic), ("trick", trick), ("equipment", equipment)):
        for _ in range(count):
            taken.append(_first_of(category=category, exclude=(*skip, *taken)))
    return tuple(taken)


def _dingpin_state(
    *,
    mine: tuple[int, ...],
    judge: int,
    draw: int = 2,
    # 缺省目标**已受伤**（本技能的合法目标池；要"没人受伤"的场景显式传满血）。
    target_hp: int = 1,
    target_hp_max: int = 4,
    target_hero: str | None = None,
    hp: int = 3,
    hp_max: int = 3,
    first_seat: int = 0,
    equip: tuple[int, ...] = (),
    judge_zone: tuple[int, ...] = (),
):
    """座位 0 = 陈群（``mine`` = 直接给定的手牌）、座位 1 = 目标。

    **牌的取用次序**：``from_state`` 的局面从**回合开头**跑起（判定 → 摸牌 → 出牌），
    故牌堆 = ``draw`` 张摸牌阶段要摸的牌（取基本牌、与 ``mine`` 不重叠）→ ``judge``
    （本技能那次判定的判定牌）→ 其余。
    """
    drawn: list[int] = []
    for _ in range(draw):
        drawn.append(_first_of(category="basic", exclude=(*mine, *equip, judge, *drawn)))
    deck = (
        *drawn,
        judge,
        *deck_excluding(PACK, *mine, *equip, *drawn, judge, *judge_zone),
    )
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hero=CHENQUN, hp=hp, hp_max=hp_max, hand=mine, equip=equip, judge=judge_zone
            ),
            SeatSpec(hero=target_hero, hp=target_hp, hp_max=target_hp_max),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=first_seat),
    )


def _dingpin_play_phase(engine) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
        and engine.pending.actor == 0
    )


def _park_dingpin(state, *, draw: int = 2):
    """构造局面 → 开到陈群的出牌阶段询问（预发动清单就在这条询问上）。"""
    engine = Engine.from_state(state, seed=1)
    engine.set_draw_phase_count(0, draw)  # 摸牌阶段的张数（缺省 2；空牌堆夹具要 0）
    return _drive(engine, _dingpin_play_phase)


def _manifest(query, skill_id: str = DINGPIN):
    return tuple((query.preactivations or {}).get(skill_id, ()))


def _pool_options(step) -> tuple[str, ...]:
    seen: list[str] = []
    for row in step.pool:
        for option in row.options:
            if option not in seen:
                seen.append(option)
    return tuple(seen)


def _confirm_dingpin(engine, *, cost: int, target: int, skill_id: str = DINGPIN):
    """在出牌阶段询问上提交复合动作（弃 ``cost``、指定 ``target``），推进回出牌阶段。"""
    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=skill_id,
            picks=(cost,),
            choices=(f"seat:{target}",),
            answers_seq=query.seq,
        )
    )
    return _drive(engine, _dingpin_play_phase)


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


def _judge_events(engine) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.JUDGE_ON
    ]


def _skill_asked(engine, skill_id: str) -> bool:
    """记录流里有没有这条技能的「是否发动」询问（挂起点的证据）。"""
    return any(
        record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and str(record.query.skill_id) == skill_id
        for record in engine.records
    )


# ---------------------------------------------------------------------------
# 形状：预发动清单（两步：card → seat）
# ---------------------------------------------------------------------------


def test_chenqun_dingpin_is_not_a_plain_candidate_and_exposes_a_two_step_manifest():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge))
    query = engine.pending

    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == DINGPIN
    ], "已翻预发动：骨架候选从 legal_actions 删掉"
    steps = _manifest(query)
    assert [step.name for step in steps] == ["card", "seat"], "两步：弃牌 → 选人"
    assert (steps[0].min, steps[0].max) == (1, 1), "代价恰好一张"
    assert set(steps[0].zones) == {Zone.HAND}, "牌面明写「手牌」⇒ 只有手牌区"


def test_chenqun_dingpin_manifest_first_step_lays_out_hand_only():
    """首步铺的是**陈群自己的手牌**（比界版少一个装备区，正是本版的读法）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    other_equip = _first_of(category="equipment", exclude=mine)
    judge = _basic_with_suit(Suit.SPADE, exclude=(*mine, other_equip))

    engine = _park_dingpin(
        _dingpin_state(mine=mine, judge=judge, equip=(other_equip,), target_hp=1, target_hp_max=4)
    )
    steps = _manifest(engine.pending)
    options = _pool_options(steps[0])

    assert set(options) == {str(card) for card in engine.state.player(0).hand}, (
        "候选面恰是手牌（摸牌阶段摸到的两张也在内）"
    )
    assert str(other_equip) not in options, "装备区那张不是代价"


def test_chenqun_dingpin_costs_exactly_one_card():
    """「弃置一张手牌」⇒ ``lo = hi = 1``；0 张不是合法动作（不给"弃 0 张"的候选）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge, target_hp=1, target_hp_max=4))
    steps = _manifest(engine.pending)
    assert (steps[0].min, steps[0].max) == (1, 1)

    with pytest.raises(IllegalActionError):
        engine.submit(
            ActivateSkill(
                skill_id=DINGPIN,
                picks=(),
                choices=("seat:1",),
                answers_seq=engine.pending.seq,
            )
        )
    with pytest.raises(IllegalActionError):
        engine.submit(
            ActivateSkill(
                skill_id=DINGPIN,
                picks=(mine[0], mine[1]),
                choices=("seat:1",),
                answers_seq=engine.pending.seq,
            )
        )


def test_chenqun_dingpin_never_costs_an_equipment_card():
    """牌面写「一张**手牌**」⇒ 不套"手牌 + 装备区"的区域默认：装备区的牌永远不是代价。"""
    mine = _hand_of(basic=4)
    other_equip = _first_of(category="equipment", exclude=mine)
    judge = _basic_with_suit(Suit.SPADE, exclude=(*mine, other_equip))

    state = _dingpin_state(
        mine=mine,
        judge=judge,
        equip=(other_equip,),
        target_hp=1,
        target_hp_max=4,
    )
    # 基本牌那一类已经用掉 ⇒ 手牌一张都当不了代价；装备区那张**大类可用**却救不了场。
    state.player(0).turn_card_entries.append((-2, "basic"))

    engine = _park_dingpin(state)

    assert not _manifest(engine.pending), "装备区的牌不算代价 ⇒ 付不起 ⇒ 清单不出现"


def test_chenqun_dingpin_is_not_offered_without_a_legal_cost_card():
    """手牌为空（摸牌阶段 0 张）⇒ 候选阶段不产出入口。"""
    mine = ()
    judge = _basic_with_suit(Suit.SPADE)

    engine = _park_dingpin(
        _dingpin_state(mine=mine, judge=judge, draw=0, target_hp=1, target_hp_max=4), draw=0
    )

    assert not engine.state.player(0).hand, "手牌真的空"
    assert not _manifest(engine.pending), "一张合法代价都没有 ⇒ 清单不出现"


def test_chenqun_dingpin_is_not_offered_without_an_injured_target():
    """「一名**已受伤**的角色」⇒ 全场无伤者时池空、整条取消（不发空询问）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(
        _dingpin_state(mine=mine, judge=judge, target_hp=4, target_hp_max=4), draw=2
    )

    assert not _manifest(engine.pending), "没有已受伤角色 ⇒ 清单不出现"


def test_chenqun_dingpin_is_not_offered_outside_my_play_phase():
    """「出牌阶段」没写谁的阶段 ⇒ 只在陈群自己的出牌阶段（别人的回合里没有清单）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)
    state = _dingpin_state(
        mine=mine, judge=judge, target_hp=1, target_hp_max=4, first_seat=1
    )

    engine = _drive(
        Engine.from_state(state, seed=1),
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE,
    )

    assert engine.pending.actor == 1, "现在是座位 1 的出牌阶段"
    assert not _manifest(engine.pending), "不是我的出牌阶段"


def test_chenqun_dingpin_has_no_entry_when_the_play_phase_is_skipped():
    """【乐不思蜀】非红桃 ⇒ 出牌阶段整段不存在 ⇒ 【定品】没有入口。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    indulgence = cards_of_type(PACK, "indulgence")[0]
    top = next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in (*mine, indulgence)
        and PACK.deck.cards[card_id].suit is not Suit.HEART
    )
    state = _dingpin_state(
        mine=mine,
        judge=top,
        target_hp=1,
        target_hp_max=4,
        judge_zone=(indulgence,),
    )

    engine = _drive(
        Engine.from_state(state, seed=1),
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 1,
    )

    play_queries = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind is QueryKind.PLAY_PHASE
        and record.query.actor == 0
    ]
    assert not play_queries, "跳过出牌阶段 ⇒ 连询问都没有"
    assert not engine.state.player(0).turn_card_entries, "回合末照常清空"
    assert not engine.state.player(0).dingpin_barred_targets


def test_chenqun_dingpin_declining_the_manifest_costs_nothing():
    """玩家在清单里不动 ⇒ 零副作用（成本未搬、无判定、无禁令）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge, target_hp=1, target_hp_max=4))

    assert set(mine) <= set(engine.state.player(0).hand), "手牌一张没少"
    assert not [card for card in engine.state.discard if card in mine], "成本没有被搬走"
    assert not engine.state.player(0).dingpin_barred_targets
    assert _judge_events(engine) == [], "没有判定"


def test_chenqun_dingpin_takes_the_only_injured_seat_directly():
    """只剩一条合法路（陈群满血、只有座位 1 受伤）⇒ 座位步只有一个候选。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge, target_hp=1, target_hp_max=4))
    steps = _manifest(engine.pending)

    assert _pool_options(steps[1]) == ("seat:1",), "单候选 ⇒ 前端直提、不铺面"


def test_chenqun_dingpin_can_target_itself_when_injured():
    """「一名角色」含自己（L776①）：陈群自己受伤时也在池里，且能真的指定自己。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(
        _dingpin_state(mine=mine, judge=judge, hp=1, hp_max=3, target_hp=1, target_hp_max=4)
    )
    steps = _manifest(engine.pending)
    assert "seat:0" in _pool_options(steps[1]), "自己受伤 ⇒ 自己在池里"
    assert "seat:1" in _pool_options(steps[1])

    before = len(engine.state.player(0).hand)
    engine = _confirm_dingpin(engine, cost=mine[0], target=0)

    assert len(engine.state.player(0).hand) == before - 1 + 2, (
        "弃 1 摸 X（自己已损失 2 点体力）"
    )


def test_chenqun_dingpin_treats_a_reduced_hp_max_as_injured():
    """「已受伤」只看 ``hp < hp_max``，不看"上限有没有被减过"（账本 L741）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    full_with_reduced_max = _dingpin_state(
        mine=mine, judge=judge, target_hp=2, target_hp_max=2
    )
    engine = _park_dingpin(full_with_reduced_max)
    assert not _manifest(engine.pending), "上限被减到 2 但体力也满 2 ⇒ 不是「已受伤」"

    hurt_with_reduced_max = _dingpin_state(
        mine=mine, judge=judge, target_hp=3, target_hp_max=5
    )
    engine = _park_dingpin(hurt_with_reduced_max)
    steps = _manifest(engine.pending)
    assert "seat:1" in _pool_options(steps[1]), "上限被减过、体力低于上限 ⇒ 仍是「已受伤」"


# ---------------------------------------------------------------------------
# 两分支：黑（摸 X + 禁令）/ 红（你翻面）
# ---------------------------------------------------------------------------


def test_chenqun_dingpin_discards_one_card_and_judges_for_the_target():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    cost = mine[0]
    judge = _basic_with_suit(Suit.CLUB, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge, target_hp=3, target_hp_max=4))
    before = len(engine.state.player(1).hand)

    engine = _confirm_dingpin(engine, cost=cost, target=1)

    assert ("hand", "discard", "DISCARD") in _moves_of(engine, cost), "成本进弃牌堆"
    assert ran_timing(engine, Timing.JUDGE_ON), "确实进行了一次判定"
    assert len(engine.state.player(1).hand) == before + 1, "黑判定：目标摸 X（已损失 1 点）"


def test_chenqun_dingpin_black_draws_the_lost_hp():
    """X = 该角色**已损失**的体力值 = 上限 − 当前体力（本版**不是** min(体力值, 3)）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge, target_hp=1, target_hp_max=4))
    before = len(engine.state.player(1).hand)

    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    assert len(engine.state.player(1).hand) == before + 3, "已损失 3 点体力 ⇒ 摸 3 张"


def test_chenqun_dingpin_black_draws_more_than_three_when_the_target_is_badly_hurt():
    """本版**没有**"至多 3"的钳制（与界版的差别之一）：已损失 5 点体力就摸 5 张。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge, target_hp=1, target_hp_max=6))
    before = len(engine.state.player(1).hand)

    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    assert len(engine.state.player(1).hand) == before + 5, "5 > 3 也照摸"


def test_chenqun_dingpin_black_bars_the_target_for_the_rest_of_the_turn():
    """黑判定后该目标出池；陈群自己受伤 ⇒ 清单仍在（池里还有他自己）。"""
    mine = _hand_of(basic=2, trick=1, equipment=1)
    judge = _basic_with_suit(Suit.CLUB, exclude=mine)

    engine = _park_dingpin(
        _dingpin_state(mine=mine, judge=judge, hp=2, hp_max=3, target_hp=1, target_hp_max=4)
    )
    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    steps = _manifest(engine.pending)
    assert steps, "还有别的类可弃 ∧ 自己已受伤 ⇒ 清单仍在"
    assert "seat:1" not in _pool_options(steps[1]), "黑判定后该目标出池"
    assert "seat:0" in _pool_options(steps[1]), "其他已受伤角色仍在池里"


def test_chenqun_dingpin_bar_is_idempotent():
    """同一目标在同一回合被禁两次 ⇒ 幂等（已实现"已禁过返回 False、座位升序"）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _dingpin_state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    engine = Engine.from_state(state, seed=1)

    assert engine.bar_dingpin_target(0, 1) is True
    assert engine.bar_dingpin_target(0, 0) is True
    assert engine.bar_dingpin_target(0, 1) is False, "重复禁同一目标 ⇒ 空操作"
    assert engine.state.player(0).dingpin_barred_targets == [0, 1], "座位升序"


def _kill_target_during_the_judgment(state):
    """把座位 1 在**判定窗口内**标成阵亡（``alive=False``；死亡流程不是本条的被测面）。

    做法 = 给座位 0 挂【鬼才】，让判定帧的改判窗口问出一次 ``RESPOND``；驱动循环在那一问
    之前把目标标死，再答"放弃改判"。与 ``tests/test_hero_extra_turn.py`` 的
    "构造局面的捷径"同一条手法（那条注释逐字写"死亡流程本身不是本条的被测面"）。
    """
    state.player(0).granted_skills.append("hero.simayi.guicai")
    engine = _park_dingpin(state)
    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=DINGPIN,
            picks=(state.player(0).hand[0],),
            choices=("seat:1",),
            answers_seq=query.seq,
        )
    )
    for _ in range(3000):
        if engine.finished or _dingpin_play_phase(engine):
            return engine
        pending = engine.pending
        if pending is None:
            engine._step()
            continue
        if pending.kind == QueryKind.RESPOND and pending.cause is WindowCause.REJUDGE:
            state.player(1).alive = False
        engine.submit(_answer(pending))
    raise AssertionError("步数上限内没有回到出牌阶段")


def test_chenqun_dingpin_bar_is_written_even_if_the_target_dies():
    """G9②：目标在判定结算中阵亡 ⇒ 不摸，但**禁令照写**（牌面无条件）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _kill_target_during_the_judgment(
        _dingpin_state(mine=mine, judge=judge, hp=2, hp_max=3, target_hp=1, target_hp_max=4)
    )

    assert not engine.state.player(1).alive
    assert engine.state.player(0).dingpin_barred_targets == [1], "禁令照写"


def test_chenqun_dingpin_does_not_draw_for_a_dead_target():
    """目标在判定结算中阵亡 ⇒ **不摸**（``alive`` 假 ⇒ 跳过摸牌那一段）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)

    engine = _kill_target_during_the_judgment(
        _dingpin_state(
            mine=mine,
            judge=_basic_with_suit(Suit.SPADE, exclude=mine),
            hp=2,
            hp_max=3,
            target_hp=1,
            target_hp_max=4,
        )
    )

    assert not engine.state.player(1).alive
    assert engine.state.player(1).hand == [], "死者一张都没摸到"


def test_chenqun_dingpin_red_toggles_the_hero_card():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.DIAMOND, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge))
    assert not engine.state.player(0).flipped

    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    assert engine.state.player(0).flipped, "♦ ⇒ 你翻面（toggle 到背面）"


def test_chenqun_dingpin_heart_also_flips_and_does_not_refund():
    """♥ 与 ♦ 同走"你翻面"那一路；本版**没有** ♥ 撤回 ⇒ 成本的大类照旧算用掉。"""
    mine = _hand_of(basic=2, trick=1, equipment=1)
    cost, other_basic = mine[0], mine[1]
    trick = mine[2]
    judge = _basic_with_suit(Suit.HEART, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge))
    engine = _confirm_dingpin(engine, cost=cost, target=1)

    assert engine.state.player(0).flipped, "♥ ⇒ 也是「你翻面」"
    assert (cost, "basic") in engine.state.player(0).turn_card_entries, "成本照记（不退款）"
    steps = _manifest(engine.pending)
    assert str(other_basic) not in _pool_options(steps[0]), "同大类的那张仍不可弃"
    assert str(trick) in _pool_options(steps[0]), "没用过的类照常可选"


def test_chenqun_dingpin_red_does_not_end_the_current_turn():
    """在自己的回合内被翻面**不影响当回合**（翻面 = 状态位，账本 L783）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.HEART, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge))
    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    assert engine.pending is not None
    assert engine.pending.kind == QueryKind.PLAY_PHASE and engine.pending.actor == 0, (
        "仍在陈群的出牌阶段"
    )
    assert engine.state.turn_seat == 0
    assert not [
        record for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_END
    ], "没有回合结束"


def test_chenqun_dingpin_judges_exactly_once_per_activation():
    """G4：一次发动 = 一次判定 = 一段结算（逐事件一次，账本 L777）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge))
    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    assert len(_judge_events(engine)) == 1, "只判一次"
    assert len(
        [
            record
            for record in engine.records
            if record.kind == "timing" and record.timing is Timing.JUDGE_CARD_AFTER_EFFECT
        ]
    ) == 1, "只结算一段"


def test_chenqun_dingpin_the_target_is_the_judging_character():
    """G5：「令**其**进行一次判定」⇒ 判定者是被指定的那名角色（不是陈群）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park_dingpin(_dingpin_state(mine=mine, judge=judge))
    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    events = _judge_events(engine)
    assert [record.actor for record in events] == [1], "判定者是座位 1"


def test_chenqun_dingpin_x_is_the_lost_hp_and_never_negative():
    """纯函数级：X = ``hp_max - hp``，夹到 ≥ 0（判定窗口内被治满 ⇒ 0 ⇒ 不摸）。"""
    from sgs.skills.active import _chenqun_dingpin_x

    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)
    state = _dingpin_state(mine=mine, judge=judge, target_hp=1, target_hp_max=4)

    assert _chenqun_dingpin_x(state, 1) == 3, "已损失 3 点"
    state.player(1).hp = 4
    assert _chenqun_dingpin_x(state, 1) == 0, "被治满 ⇒ 0（不是负数）"
    state.player(1).hp = 6
    state.player(1).hp_max = 4
    assert _chenqun_dingpin_x(state, 1) == 0, "夹到 ≥ 0"


def test_chenqun_dingpin_a_suitless_judgment_does_nothing():
    """花色为空 ⇒ 黑 / 红都不成立（fail-closed，账本 L896）：不摸、不禁、不翻。

    引擎侧拿不到"无花色的判定牌"（判定牌来自牌堆 ⇒ 恒有花色），故这里直接调模块级
    效果函数并把帧私有判定牌写成 ``suit=None``——与本文件既有的"无点数 / 无花色"
    fail-closed 断言（:func:`test_jianying_comparison_is_fail_closed_without_rank_or_suit`）
    同一条姿态：真正会被改坏的判据只有一处。
    """
    from sgs.resolution import Frame
    from sgs.skills import active as active_module
    from sgs.skills.catalog import SKILL_JUDGMENT_CARD_KEY
    from sgs.state import FrameKind

    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _dingpin_state(mine=mine, judge=0, target_hp=1, target_hp_max=4)
    engine = Engine.from_state(state, seed=1)
    hand_before = list(state.player(1).hand)
    frame = Frame(kind=FrameKind.SKILL, data={"source": 0, "skill_id": DINGPIN})
    frame.data[active_module._CHENQUN_DINGPIN_COST_KEY] = True
    frame.data[active_module._CHENQUN_DINGPIN_JUDGED_KEY] = True
    frame.data[SKILL_JUDGMENT_CARD_KEY] = CardRef(
        card_id=0, type="slash", name="杀", suit=None, rank=None
    )

    control = active_module._chenqun_dingpin_effect(
        engine, frame, {"card": (mine[0],), "seat": (1,)}
    )

    assert control is None
    assert state.player(1).hand == hand_before, "不摸"
    assert not state.player(0).dingpin_barred_targets, "不写禁令"
    assert not state.player(0).flipped, "不翻面"


def test_chenqun_dingpin_reads_the_final_suit_after_a_rejudge():
    """判据取**改判后**那张的花色（规则 §11.1 / §11.2）：♥ 被改成黑 ⇒ 走黑那一段。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.HEART, exclude=mine)
    black = next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in (*mine, judge)
        and PACK.deck.cards[card_id].suit in (Suit.SPADE, Suit.CLUB)
    )

    state = _dingpin_state(mine=mine, judge=judge, draw=0, target_hp=1, target_hp_max=4)
    state.player(0).granted_skills.append("hero.simayi.guicai")
    # 改判要一张手牌：把那张黑牌从牌堆挪到陈群手里（成本仍然用 mine[0]；摸牌阶段 0 张，
    # 牌堆顶因此就是那张判定牌）。
    state.deck = [card_id for card_id in state.deck if card_id != black]
    state.player(0).hand.append(black)
    engine = _park_dingpin(state, draw=0)
    before = len(engine.state.player(1).hand)

    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=DINGPIN,
            picks=(mine[0],),
            choices=("seat:1",),
            answers_seq=query.seq,
        )
    )
    for _ in range(3000):
        if engine.finished or _dingpin_play_phase(engine):
            break
        pending = engine.pending
        if pending is None:
            engine._step()
            continue
        if pending.kind == QueryKind.RESPOND and pending.cause is WindowCause.REJUDGE:
            action = next(
                candidate
                for candidate in pending.legal_actions
                if isinstance(candidate, SubstituteJudgeCard)
                and candidate.card.card_id == black
            )
            engine.submit(action)
            continue
        engine.submit(_answer(pending))

    assert len(engine.state.player(1).hand) == before + 3, (
        "改成黑 ⇒ 走「摸 X」那一段（X = 3）"
    )
    assert not engine.state.player(0).flipped, "不再是红 ⇒ 不翻面"
    assert engine.state.player(0).dingpin_barred_targets == [1], "黑那一段的禁令照写"


def test_chenqun_dingpin_black_draw_into_an_empty_deck_is_a_draw():
    """G10：黑判定后的摸牌两堆皆空 ⇒ 短摸即平局（既有路径，本技能只透传）。"""
    mine = _hand_of(basic=1)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    # 目标 = 郭嘉【天妒】：判定牌进他的手牌、不进弃牌堆 ⇒ 摸牌那一刻牌堆与弃牌堆都空。
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=CHENQUN, hp=3, hp_max=3, hand=mine),
            SeatSpec(hero="guojia", hp=1, hp_max=3),
        ],
        deck=(judge,),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = _park_dingpin(state, draw=0)
    assert list(engine.state.deck) == [judge], "牌堆只剩那张判定牌"

    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=DINGPIN,
            picks=(mine[0],),
            choices=("seat:1",),
            answers_seq=query.seq,
        )
    )
    _drive(engine, _dingpin_play_phase, activate=("hero.guojia.tiandu",))

    assert engine.finished
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result == "draw" and tail.reason == "both_decks_empty"


# ---------------------------------------------------------------------------
# 座位事实：「本回合使用或弃置过的类型」（复用界版同一份，零改动）
# ---------------------------------------------------------------------------


def test_chenqun_dingpin_excludes_categories_used_this_turn():
    mine = _hand_of(basic=1, trick=1, equipment=2)

    state = _dingpin_state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    state.player(0).turn_card_entries.append((-9, "basic"))

    engine = _park_dingpin(state)
    steps = _manifest(engine.pending)

    assert str(mine[0]) not in _pool_options(steps[0]), "用过的那一类不可选"
    assert str(mine[1]) in _pool_options(steps[0]), "没用过的那一类可选"


def test_chenqun_dingpin_excludes_categories_discarded_this_turn():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _dingpin_state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    engine = Engine.from_state(state, seed=1)

    assert engine.note_turn_discard(0, (engine.state.card(mine[0]),)) == 1
    engine = _drive(engine, _dingpin_play_phase)
    steps = _manifest(engine.pending)

    assert str(mine[0]) not in _pool_options(steps[0]), "弃过的那一类不可选"
    assert str(mine[2]) in _pool_options(steps[0]), "没动过的那一类可选"


def test_chenqun_dingpin_counts_a_discard_by_its_actor_only():
    """「你弃置过的类型」的主体是**动作主体**：记在弃置者名下，不记在牌主名下。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _dingpin_state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    engine = Engine.from_state(state, seed=1)

    engine.note_turn_discard(1, (engine.state.card(mine[0]),))

    assert engine.state.player(1).turn_card_entries, "记在弃置者（座位 1）名下"
    assert not engine.state.player(0).turn_card_entries, "不记在牌主（座位 0）名下"


def test_chenqun_dingpin_records_the_discarded_card_category():
    """代价弃的是**实体牌**：记录读被弃那张牌自己的大类（不是产物）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    cost = mine[2]  # 一张装备牌

    engine = _park_dingpin(
        _dingpin_state(
            mine=mine,
            judge=_basic_with_suit(Suit.SPADE, exclude=mine),
            target_hp=1,
            target_hp_max=4,
        )
    )
    engine = _confirm_dingpin(engine, cost=cost, target=1)

    assert (cost, "equipment") in engine.state.player(0).turn_card_entries


def test_chenqun_dingpin_records_the_product_category_of_a_converted_use():
    """使用侧按**产物牌型**判大类（与账本 L758 的【涯角】同口径）：

    一张红色**装备**牌被【武圣】当【杀】用掉 ⇒ 记下的大类是「基本」，不是「装备」。
    """
    red_equip = next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if _category_of(card_id) == "equipment"
        and PACK.deck.cards[card_id].suit in (Suit.HEART, Suit.DIAMOND)
    )
    basic = _first_of(category="basic", exclude=(red_equip,))
    trick = _first_of(category="trick", exclude=(red_equip, basic))
    mine = (red_equip, basic, trick)

    state = _dingpin_state(
        mine=mine,
        judge=_basic_with_suit(Suit.SPADE, exclude=mine),
        draw=0,
        target_hp=3,
        target_hp_max=4,
    )
    state.player(0).granted_skills.append("hero.guanyu.wusheng")
    engine = _park_dingpin(state, draw=0)
    steps = (engine.pending.transform_preactivations or {}).get("hero.guanyu.wusheng")
    assert steps, "红牌当【杀】的转化清单在出牌阶段下发"

    engine.submit(
        ActivateSkill(
            skill_id="hero.guanyu.wusheng",
            picks=(red_equip,),
            choices=("seat:1",),
            answers_seq=engine.pending.seq,
        )
    )
    engine = _drive(engine, _dingpin_play_phase)

    assert [category for _, category in engine.state.player(0).turn_card_entries] == ["basic"], (
        "产物是一张【杀】⇒ 记「基本」，不是来源那张的「装备」"
    )
    steps = _manifest(engine.pending)
    assert str(basic) not in _pool_options(steps[0]), "「基本」已用过"
    assert str(trick) in _pool_options(steps[0]), "「锦囊」还没用过"


# ---------------------------------------------------------------------------
# 挂起续跑：搬牌（内联 MOVE_AFTER）/ 摸牌（内联"获得牌后"）
# ---------------------------------------------------------------------------


def test_chenqun_dingpin_resumes_once_when_a_move_hook_suspends():
    """成本弃置内联触发【连营】的「是否发动」并挂起 ⇒ 续跑不重搬、不重判。"""
    mine = (_first_of(category="basic"),)
    state = _dingpin_state(
        mine=mine,
        judge=_basic_with_suit(Suit.SPADE, exclude=mine),
        draw=0,
        target_hp=1,
        target_hp_max=4,
    )
    state.player(0).granted_skills.append("hero.jie_luxun.lianying")
    engine = _park_dingpin(state, draw=0)
    before = len(engine.state.player(1).hand)

    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    assert _skill_asked(engine, "hero.jie_luxun.lianying"), "成本弃置确实内联问了【连营】"
    assert _moves_of(engine, mine[0]) == [("hand", "discard", "DISCARD")], "成本只搬一次"
    assert len(_judge_events(engine)) == 1, "判定只开一次"
    assert len(engine.state.player(1).hand) == before + 3, "X = 3，只摸一次"


def test_chenqun_dingpin_draws_once_when_a_gain_hook_suspends():
    """目标摸牌内联触发【清俭】并挂起 ⇒ 续跑不重摸（帧私有 ``drawn`` 游标）。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _dingpin_state(
        mine=mine,
        judge=_basic_with_suit(Suit.SPADE, exclude=mine),
        target_hp=1,
        target_hp_max=4,
    )
    state.player(1).granted_skills.append("hero.jie_xiahoudun.qingjian")
    engine = _park_dingpin(state)
    before = len(engine.state.player(1).hand)

    engine = _confirm_dingpin(engine, cost=mine[0], target=1)

    assert _skill_asked(engine, "hero.jie_xiahoudun.qingjian"), "摸牌确实内联问了【清俭】"
    assert len(engine.state.player(1).hand) == before + 3, "只摸一次（重入不重摸）"
    assert engine.state.player(0).dingpin_barred_targets == [1], "禁令仍落在这一次发动里"


# ---------------------------------------------------------------------------
# 复位点：两个回合之间（额外回合也是"另一个游戏回合"）
# ---------------------------------------------------------------------------


def test_chenqun_dingpin_facts_reset_between_two_turns():
    """上一回合的「用过的大类」与「禁令」都清空 ⇒ 下一回合同一目标可再被指定。"""
    mine = _hand_of(basic=2, trick=1, equipment=1)
    other_basic = mine[1]
    state = _dingpin_state(
        mine=mine,
        judge=_basic_with_suit(Suit.SPADE, exclude=mine),
        hp=2,
        hp_max=3,
        target_hp=1,
        target_hp_max=4,
    )
    engine = _park_dingpin(state)
    engine = _confirm_dingpin(engine, cost=mine[0], target=1)
    assert engine.state.player(0).dingpin_barred_targets == [1]
    assert engine.state.player(0).turn_card_entries

    start = engine.pending.seq
    engine = _drive(
        engine,
        lambda e: _dingpin_play_phase(e) and e.pending.seq > start,
    )

    assert not engine.state.player(0).dingpin_barred_targets, "禁令在回合末清空"
    assert not engine.state.player(0).turn_card_entries, "「用过 / 弃过」在回合末清空"
    steps = _manifest(engine.pending)
    assert steps, "新回合的清单照常出现"
    assert "seat:1" in _pool_options(steps[1]), "上一回合的禁令不跨回合"
    assert str(other_basic) in _pool_options(steps[0]), "同一大类在新回合重新可用"


# ---------------------------------------------------------------------------
# A4 的机械钉：两条 SkillDef 各写各的文案
# ---------------------------------------------------------------------------


def test_chenqun_dingpin_is_declared_with_its_own_wording():
    """A4：本版 ``requirement`` / ``step_requirements`` 与界版**逐字不同**，且短 id 表未改。"""
    from sgs.skills import CATALOG
    from sgs.skills.active import _REQUIREMENTS, _STEP_REQUIREMENTS

    ours = CATALOG[DINGPIN]
    jie = CATALOG[JIE_DINGPIN]

    assert ours.requirement and jie.requirement
    assert ours.requirement != jie.requirement, "两句牌面不同 ⇒ 不能共用界版那一句"
    assert "已损失" in ours.requirement, "本版的 X 口径"
    assert "至多为3" not in ours.requirement, "那是界版的 X 口径"
    assert dict(ours.step_requirements) != dict(jie.step_requirements)
    assert "手牌" in ours.step_requirements["card"]
    assert "已受伤" in ours.step_requirements["seat"]
    # 短 id 两张表一字未改：界版那一条仍与表逐字相同（改了两版会一起改掉）。
    assert jie.requirement == _REQUIREMENTS["dingpin"]
    assert dict(jie.step_requirements) == _STEP_REQUIREMENTS["dingpin"]


# ===========================================================================
# 一将成名-2014·顾雍【慎行】（yijiang-2014 票 03）
#
# 牌面（用户 `技能.txt` 逐字）：
#
#     出牌阶段，你可以弃置两张牌，然后摸一张牌。
#
# 用户 2026-11-09 签 Q-G1 = A（**无次数上限**）、Q-G2 = A（【秉壹】按源文件、本票摘出）、
# Q-G3 = 可以（三行账本提案）。与界顾雍移动版【慎行】**同短 id、牌面不同**（A4）：
#
# - 本版**没有**「出牌阶段限X次（X为你的体力值）」⇒ 同一出牌阶段可反复发动；
# - 本版**恒摸一张**（不读颜色 ⇒ 没有同色 / 异色的两支）；
# - 代价「两张牌」的区域默认 = 手牌 + 装备区（账本 L775②、L674），可跨区各取一张。
#
# 【秉壹】命中 C3（座位多选面未落地）⇒ 摘出到 `09-guyong-bingyi.md`，本票**不声明**
# 它的数据行、不写它的断言。
#
# 判据全走**外部缝**：预发动清单（``query.preactivations``）、记录流里的搬牌 / 摸牌 / 询问、
# 座位上的手牌 / 装备区。不断言内部结构，也不 mock。
# ===========================================================================

GUYONG = "guyong"
SHENXING = "hero.guyong.shenxing"
JIE_SHENXING = "hero.jie_guyong_mobile.shenxing"


def _card_of_color(color: str, *, exclude: tuple[int, ...] = ()) -> int:
    """挑一张指定**颜色**的牌（``color`` = ``"red"`` / ``"black"``）。"""
    for card_id in range(len(PACK.deck.cards)):
        if card_id in exclude:
            continue
        actual = color_of_suit(PACK.deck.cards[card_id].suit)
        if actual is not None and actual.value == color:
            return card_id
    raise AssertionError(f"标准 108 里没有 {color} 的牌")


def _guyong_state(
    *,
    mine: tuple[int, ...],
    equip: tuple[int, ...] = (),
    draw: int = 2,
    after: int = 2,
    hp: int = 3,
    hp_max: int = 3,
    first_seat: int = 0,
    rest: bool = True,
):
    """座位 0 = 顾雍（``mine`` = 直接给定的手牌）、座位 1 = 空座。

    **牌的取用次序**：``from_state`` 的局面从**回合开头**跑起（准备 → 判定 → 摸牌 → 出牌），
    故牌堆 = ``draw`` 张摸牌阶段要摸的牌 → ``after`` 张留给【慎行】的摸牌 → 其余。
    """
    drawn: list[int] = []
    for _ in range(draw):
        drawn.append(_first_of(category="basic", exclude=(*mine, *equip, *drawn)))
    pending_draw: list[int] = []
    for _ in range(after):
        pending_draw.append(
            _first_of(category="basic", exclude=(*mine, *equip, *drawn, *pending_draw))
        )
    taken = (*mine, *equip, *drawn, *pending_draw)
    deck = (*drawn, *pending_draw, *(deck_excluding(PACK, *taken) if rest else ()))
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=GUYONG, hp=hp, hp_max=hp_max, hand=mine, equip=equip),
            SeatSpec(),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=first_seat),
    )


def _guyong_play_phase(engine) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
        and engine.pending.actor == 0
    )


def _park_guyong(state, *, draw: int = 2):
    """构造局面 → 开到顾雍的出牌阶段询问（预发动清单就在这条询问上）。"""
    engine = Engine.from_state(state, seed=1)
    engine.set_draw_phase_count(0, draw)
    return _drive(engine, _guyong_play_phase)


def _activate_guyong(engine, picks=(), choices=(), skill_id: str = SHENXING):
    """在出牌阶段询问上提交复合动作（预发动确认），推进回出牌阶段。"""
    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=skill_id,
            picks=tuple(picks),
            choices=tuple(choices),
            answers_seq=query.seq,
        )
    )
    return _drive(engine, _guyong_play_phase)


def _hand_size(engine, seat: int = 0) -> int:
    return len(engine.state.player(seat).hand)


def _moves_with(engine, *card_ids: int) -> list:
    """牌面恰好是 ``card_ids`` 这一批的 ``card_move`` 记录（同一来源区的牌同批搬走）。"""
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and {card.card_id for card in record.cards} == set(card_ids)
    ]


def _drawn_batches(engine) -> list:
    """摸牌落成的 ``card_move`` 记录（``reason = DRAW``；一次 ``draw_for_hand`` 一批）。"""
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DRAW"
    ]


# ---------------------------------------------------------------------------
# 形状：预发动清单（单步：恰两张，手牌 ∪ 装备区）
# ---------------------------------------------------------------------------


def test_shenxing_exposes_a_single_step_manifest_that_takes_exactly_two_cards():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    engine = _park_guyong(_guyong_state(mine=mine))
    query = engine.pending

    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == SHENXING
    ], "已翻预发动：骨架候选从 legal_actions 删掉"
    steps = _manifest(query, SHENXING)
    assert [step.name for step in steps] == ["card"], "单步：选两张牌"
    assert (steps[0].min, steps[0].max) == (2, 2), "代价恰好两张"
    assert set(steps[0].zones) == {Zone.HAND, Zone.EQUIP}, "「两张牌」= 手牌 ∪ 装备区"


def test_guyong_wording_is_its_own_not_the_jie_versions():
    """A4：本版 ``requirement`` / ``step_requirements`` 逐字取自己那一版，短 id 表未改。"""
    from sgs.skills import CATALOG, flow_for
    from sgs.skills.active import _REQUIREMENTS, _STEP_REQUIREMENTS

    ours = CATALOG[SHENXING]
    jie = CATALOG[JIE_SHENXING]

    assert ours.requirement == "出牌阶段，你可以弃置两张牌，然后摸一张牌"
    assert "颜色" not in ours.requirement, "本版没有颜色那一句"
    assert "限" not in ours.requirement, "本版没有「限X次」"
    assert ours.requirement != jie.requirement, "两句牌面不同 ⇒ 不能共用界版那一句"
    assert dict(ours.step_requirements) == {"card": "弃置两张牌（手牌 / 装备区）"}, (
        "这一句两版同值，仍**各自显式声明**（不读短 id 表，防将来漂移）"
    )
    # 短 id 两张表一字未改：界版那一条仍与表逐字相同（改了两版会一起改掉）。
    assert jie.requirement == _REQUIREMENTS["shenxing"]
    assert dict(jie.step_requirements) == _STEP_REQUIREMENTS["shenxing"]
    # 流水线也必须各叫各的（`tests/test_ask_points.py` 机械核"全部模块级 flow 名两两不同"）。
    ours_flow = flow_for(SHENXING)
    jie_flow = flow_for(JIE_SHENXING)
    assert ours_flow is not None and jie_flow is not None
    assert ours_flow.name == "guyong_shenxing"
    assert ours_flow.name != jie_flow.name


# ---------------------------------------------------------------------------
# 效果：弃两张 → **恒**摸一张（不读颜色、不读体力）
# ---------------------------------------------------------------------------


def test_shenxing_discards_two_and_draws_exactly_one():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    engine = _park_guyong(_guyong_state(mine=mine))
    before = _hand_size(engine)
    draws_before = len(_drawn_batches(engine))

    engine = _activate_guyong(engine, picks=(mine[0], mine[1]))

    assert mine[0] in engine.state.discard and mine[1] in engine.state.discard, (
        "代价两张都进了弃牌堆"
    )
    cost = _moves_with(engine, mine[0], mine[1])
    assert len(cost) == 1 and cost[0].from_zone.zone is Zone.HAND, (
        "两张**同一批**搬走（不是逐张问、也不是逐张搬）"
    )
    drawn = _drawn_batches(engine)[draws_before:]
    assert len(drawn) == 1, "只落一次摸牌"
    assert len(drawn[0].cards) == 1, "恰好摸一张"
    assert _hand_size(engine) == before - 2 + 1, "弃两张、摸一张"


def test_shenxing_draws_one_whatever_the_colors_are():
    """本版**没有**颜色那一句：同色与异色各跑一次，结果必须一样（恒摸一张）。"""
    red1 = _card_of_color("red")
    red2 = _card_of_color("red", exclude=(red1,))
    black = _card_of_color("black", exclude=(red1, red2))

    same_color = (red1, red2)
    mixed = (red1, black)
    assert (
        color_of_suit(PACK.deck.cards[same_color[0]].suit)
        == color_of_suit(PACK.deck.cards[same_color[1]].suit)
    ), "同色那一对的夹具自校"
    assert (
        color_of_suit(PACK.deck.cards[mixed[0]].suit)
        != color_of_suit(PACK.deck.cards[mixed[1]].suit)
    ), "异色那一对的夹具自校"

    outcomes: list[int] = []
    for pair in (same_color, mixed):
        mine = (*pair, *_hand_of(basic=2, skip=pair))
        engine = _park_guyong(_guyong_state(mine=mine))
        before = _hand_size(engine)
        engine = _activate_guyong(engine, picks=pair)
        outcomes.append(_hand_size(engine) - before)

    assert outcomes == [-1, -1], "同色 / 异色都是净 −1（弃 2 摸 1，无颜色分支）"


def test_shenxing_may_pay_one_hand_card_and_one_equipment_card():
    equip_card = _first_of(category="equipment")
    mine = _hand_of(basic=2, trick=1, skip=(equip_card,))
    engine = _park_guyong(_guyong_state(mine=mine, equip=(equip_card,)))
    before = _hand_size(engine)
    assert engine.state.player(0).equip, "装备区那一张确实在场上"

    engine = _activate_guyong(engine, picks=(mine[0], equip_card))

    assert _moves_of(engine, equip_card) == [("equip", "discard", "DISCARD")], (
        "装备区的牌按自己的来源区搬走（两张可跨区各取一张 ⇒ 一次 move_card 只走一个来源区）"
    )
    assert _moves_of(engine, mine[0]) == [("hand", "discard", "DISCARD")]
    assert not engine.state.player(0).equip, "装备区那张已经离开"
    assert _hand_size(engine) == before - 1 + 1, "手牌那张是代价、摸一张补回"


def test_shenxing_may_pay_with_the_last_equipment_card():
    """物理上合法：弃掉**唯一**一张装备牌会让装备技能随之消失——这是代价，不是缺陷。"""
    equip_card = _first_of(category="equipment")
    mine = _hand_of(basic=2, trick=1, skip=(equip_card,))
    engine = _park_guyong(_guyong_state(mine=mine, equip=(equip_card,)))

    engine = _activate_guyong(engine, picks=(mine[0], equip_card))

    assert equip_card in engine.state.discard
    assert not engine.state.player(0).equip, "装备区空了"
    assert mine[0] in engine.state.discard


def test_shenxing_draws_one_even_after_the_equipment_zone_is_emptied():
    """G9：效果不读任何场况（只有绝对值 1）；弃空装备区后照样摸一张。"""
    first = _first_of(category="equipment")
    second = _first_of(category="equipment", exclude=(first,))
    mine = _hand_of(basic=2, trick=1, skip=(first, second))
    engine = _park_guyong(_guyong_state(mine=mine, equip=(first, second)))
    before = _hand_size(engine)

    engine = _activate_guyong(engine, picks=(first, second))

    assert not engine.state.player(0).equip, "两张代价都来自装备区 ⇒ 装备区已空"
    assert _hand_size(engine) == before + 1, "手牌只多了摸的那一张"


# ---------------------------------------------------------------------------
# 入口：付不起就不产出 / 取消零副作用 / **无次数上限**（Q-G1 = A）
# ---------------------------------------------------------------------------


def test_shenxing_is_not_offered_with_fewer_than_two_payable_cards():
    """G1：手牌 1 张、装备区 0 张 ⇒ 凑不出两张 ⇒ 清单不出现（连"是否发动"都没有）。"""
    mine = (_first_of(category="basic"),)
    engine = _park_guyong(_guyong_state(mine=mine, draw=0), draw=0)

    assert not _manifest(engine.pending, SHENXING), "少于两张 ⇒ 付不起"


def test_shenxing_counts_equipment_cards_as_payable():
    """同一判据的另一半：手牌只有 1 张 + 装备区 1 张 ⇒ 凑得出两张、清单出现。"""
    equip_card = _first_of(category="equipment")
    mine = (_first_of(category="basic", exclude=(equip_card,)),)
    engine = _park_guyong(_guyong_state(mine=mine, equip=(equip_card,), draw=0), draw=0)

    assert _manifest(engine.pending, SHENXING), "手牌 + 装备区合计两张 ⇒ 付得起"


def test_shenxing_cancel_leaves_the_hand_untouched():
    """「可以」含放弃：取消预发动零记录、零副作用（本版也没有次数可消耗）。"""
    mine = _hand_of(basic=2)
    engine = _park_guyong(_guyong_state(mine=mine, draw=0), draw=0)
    before = _hand_size(engine)

    assert _manifest(engine.pending, SHENXING), "取消 ≠ 用掉次数：清单仍在"
    engine.submit(
        next(action for action in engine.pending.legal_actions if isinstance(action, EndPhase))
    )
    engine = _drive(
        engine,
        lambda e: (
            e.pending is not None
            and e.pending.kind == QueryKind.PLAY_PHASE
            and e.pending.actor == 1
        ),
    )

    assert _hand_size(engine) == before, "取消后手牌一张没动"
    assert not [card for card in mine if card in engine.state.discard], "取消不弃牌"
    assert not [key for key in engine.state.skill_state if SHENXING in key], (
        "取消不写任何阶段位"
    )


def test_shenxing_may_be_used_twice_in_the_same_play_phase():
    """Q-G1 = A：**无次数上限** —— 1 体力（界版的 X = 1）下仍能连发三次。"""
    mine = _hand_of(basic=4, trick=2, equipment=1)
    engine = _park_guyong(_guyong_state(mine=mine, after=4, hp=1, hp_max=3))
    before = _hand_size(engine)

    for used in range(3):
        assert _manifest(engine.pending, SHENXING), f"第 {used + 1} 次仍该可发动（无上限）"
        engine = _activate_guyong(engine, picks=(mine[used * 2], mine[used * 2 + 1]))

    assert _hand_size(engine) == before - 3, "三次各净 −1 张（弃 2 摸 1）"
    assert _manifest(engine.pending, SHENXING), "本版没有上限 ⇒ 只要付得起就还能发动"


def test_shenxing_does_not_reset_or_touch_the_jie_versions_phase_counter():
    """A4：两条技能**共用 CATALOG 但不共用状态** —— 本版不写计数位段、也不动界版那一格。"""
    mine = _hand_of(basic=4, trick=2, equipment=2)
    state = _guyong_state(mine=mine, after=6, hp=1, hp_max=3)
    state.player(0).granted_skills.append(JIE_SHENXING)
    engine = _park_guyong(state)

    assert _manifest(engine.pending, SHENXING)
    assert _manifest(engine.pending, JIE_SHENXING), "界版的 X = 1 次清单同时在场"

    engine = _activate_guyong(engine, picks=(mine[0], mine[1]))
    assert _manifest(engine.pending, JIE_SHENXING), "本版发一次不消耗界版的 X 次"
    assert _manifest(engine.pending, SHENXING)

    engine = _activate_guyong(engine, picks=(mine[2], mine[3]), skill_id=JIE_SHENXING)
    assert not _manifest(engine.pending, JIE_SHENXING), "界版用满 X = 1 次 ⇒ 自己的清单消失"
    assert _manifest(engine.pending, SHENXING), "界版用满不影响本版（无上限）"

    keys = list(engine.state.skill_state)
    assert not [key for key in keys if "hero.guyong.shenxing" in key], (
        "本版 `once_per_phase=False` ⇒ 一个阶段位都不写"
    )
    assert [key for key in keys if JIE_SHENXING in key], "界版的计数位段只记在它自己那一格"


# ---------------------------------------------------------------------------
# 触发粒度（G4）/ 挂起续跑（G7）/ 收尾（G10）
# ---------------------------------------------------------------------------

LIANYING = "hero.jie_luxun.lianying"
QINGJIAN = "hero.jie_xiahoudun.qingjian"


def test_shenxing_asks_once_per_activation():
    """G4：逐次发动 —— 一次发动 = 一次代价 + 一次摸牌（不逐张问、不逐张搬）。"""
    mine = _hand_of(basic=2, trick=2)
    engine = _park_guyong(_guyong_state(mine=mine, draw=0), draw=0)
    before = _hand_size(engine)

    engine = _activate_guyong(engine, picks=(mine[0], mine[1]))
    first = _drawn_batches(engine)
    assert not [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SELECT_CARDS.value
        and str(record.query.skill_id) == SHENXING
    ], "已翻预发动：两张在**一个**复合动作里交齐，不会逐张再问"
    assert len(_moves_with(engine, mine[0], mine[1])) == 1, "代价只搬一批"
    assert len(first) == 1 and len(first[0].cards) == 1, "只摸一次、只摸一张"

    engine = _activate_guyong(engine, picks=(mine[2], mine[3]))

    assert len(_moves_with(engine, mine[2], mine[3])) == 1, "第二次发动也只搬自己那一批"
    assert len(_drawn_batches(engine)) == len(first) + 1, "两次发动 = 两次摸牌"
    assert _hand_size(engine) == before - 2, "两次各净 −1 张"


def test_shenxing_does_not_move_twice_when_a_move_hook_suspends():
    """G7：成本弃置内联触发【连营】并挂起 ⇒ 续跑不重搬、也不漏摸。"""
    mine = _hand_of(basic=2)
    state = _guyong_state(mine=mine, draw=0, after=1)
    state.player(0).granted_skills.append(LIANYING)
    engine = _park_guyong(state, draw=0)

    engine = _activate_guyong(engine, picks=mine)

    assert _skill_asked(engine, LIANYING), "成本弃置确实内联问了【连营】"
    assert len(_moves_with(engine, *mine)) == 1, "成本只搬一次（重入不重做）"
    assert _hand_size(engine) == 1, "两张全部付出、摸牌只摸一张（不重摸）"


def test_shenxing_does_not_draw_twice_when_a_gain_hook_suspends():
    """G7：摸牌内联触发【清俭】并挂起 ⇒ 续跑不重摸（帧私有游标）。"""
    mine = _hand_of(basic=2, trick=2)
    state = _guyong_state(mine=mine, draw=0, after=2)
    state.player(0).granted_skills.append(QINGJIAN)
    engine = _park_guyong(state, draw=0)
    before = _hand_size(engine)

    engine = _activate_guyong(engine, picks=(mine[0], mine[1]))

    assert _skill_asked(engine, QINGJIAN), "摸牌确实内联问了【清俭】"
    assert _hand_size(engine) == before - 2 + 1, "只摸一次（重入不重摸）"


class _EmptyTheDiscardAtTheCostHook:
    """在【连营】的内联询问那一刻把弃牌堆腾空（**只**为走到引擎既有的短摸路径）。

    本技能先弃两张、再摸一张，而弃掉的牌**必然**落进弃牌堆 ⇒ 纯正常路径下，摸牌那一刻
    两堆不可能同时为空（弃牌堆里恒有刚付的这两张）。G10 的"两堆皆空 ⇒ 平局"因此要在这里
    腾空弃牌堆才走得到；被测的仍是**引擎既有**的那条路径：技能把 ``draw_for_hand`` 的结局
    如实透传（不吞掉、不重摸、不报错）。
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        self.cleared = False

    def respond(self, query):
        if (
            not self.cleared
            and query.kind == QueryKind.SKILL_ACTIVATION
            and str(query.skill_id) == LIANYING
        ):
            self.cleared = True
            self.engine.state.discard.clear()
        return _answer(query)


def test_shenxing_short_deck_ends_in_a_draw():
    """G10：摸牌那一刻两堆皆空 ⇒ 短摸即平局（既有路径，技能只透传）。"""
    mine = _hand_of(basic=2)
    state = _guyong_state(mine=mine, draw=0, after=0, rest=False)
    state.player(0).granted_skills.append(LIANYING)
    engine = Engine.from_state(state, seed=1)
    engine.set_draw_phase_count(0, 0)
    engine = _drive(engine, _guyong_play_phase)
    assert not engine.state.deck and not engine.state.discard, "开局两堆皆空"

    engine.submit(
        ActivateSkill(skill_id=SHENXING, picks=mine, answers_seq=engine.pending.seq)
    )
    driver = _EmptyTheDiscardAtTheCostHook(engine)
    engine = run_queries(engine, [driver, driver], lambda e: e.finished)

    assert driver.cleared, "成本弃置确实内联问了【连营】（腾空点就在这里）"
    assert engine.finished
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result == "draw" and tail.reason == "both_decks_empty"


# ===========================================================================
# 一将成名-2014·顾雍【秉壹】（yijiang-2014 票 09，W5b；座位多选面的首个真载体）
#
# 牌面（用户 `技能.txt` 逐字，`03-guyong.md` §0）：
#
#     结束阶段，你可以展示所有手牌。若你的手牌颜色均相同，
#     你令至多等同于你手牌数名角色各摸一张牌。
#
# 用户 2026-11-09 签 Q-G2 = A（按用户源文件这一版）⇒ **只看颜色**（没有界版「或类别」
# 那一支）、座位池**含自己**、N = 展示那一刻的手牌数。两条已签死的读法直接沿用：
# 1 张手牌「均相同」= 平凡成立（`04-bingyi-uniform.md` 的 `## Answer`）、0 张手牌
# **不给入口**（`02-guyong.md` 的 Q3 = A）。「至多」= 上限 + 可放弃（账本 L775 / L753）。
#
# 交互面 = W1 的**座位多选面**（`.scratch/chenqun-guyong/issues/05-seat-multi-select.md`）：
# 服务端只需 `Engine.ask_options(options=("seat:N", …), min_selected=1, max_selected=N,
# optional=True)`——`option_seats` 非空 ⇒ 界面进牌桌多选、一次提交；「至多」的放弃出口是
# 候选里那条 `Pass`。本票**不碰** `tools/sgs_web/static/`。
#
# 判据全走**外部缝**：记录流里的 `card_reveal` / `card_move`（DRAW）/ 询问、座位上的手牌。
# 不断言内部结构，也不 mock。
# ===========================================================================

BINGYI = "hero.guyong.bingyi"


def _bingyi_hand(*colors: str) -> tuple[int, ...]:
    """按给定颜色顺序凑一手牌（``"red"`` / ``"black"``；标准 108 里两种颜色都够用）。"""
    taken: list[int] = []
    for color in colors:
        taken.append(_card_of_color(color, exclude=tuple(taken)))
    return tuple(taken)


def _basic_of_color(color: str, *, exclude: tuple[int, ...] = ()) -> int:
    """一张**基本牌**里颜色 = ``color`` 的牌（「同类别、异颜色」那一条要用）。"""
    for card_id in range(len(PACK.deck.cards)):
        if card_id in exclude:
            continue
        actual = color_of_suit(PACK.deck.cards[card_id].suit)
        if actual is not None and actual.value == color:
            if card_category(PACK.pack_id, PACK.deck.cards[card_id].type) is Category.BASIC:
                return card_id
    raise AssertionError(f"标准 108 里没有颜色 {color} 的基本牌")


def _bingyi_state(
    *,
    mine: tuple[int, ...],
    seats: int = 2,
    turn_seat: int = 0,
    who: int = 0,
    rest: bool = True,
):
    """座位 ``who`` = 顾雍（``mine`` = 直接给定的手牌），其余座位空着。

    摸牌阶段的张数由 :func:`_park_bingyi` 置 0 ⇒ **结束阶段的手牌 = ``mine``**（可数的颜色
    组合）；体力 3 / 上限 3 ⇒ 手牌 ≤ 3 时弃牌阶段一张都不弃，故 N 也只由 ``mine`` 决定。
    """
    specs = [SeatSpec() for _ in range(seats)]
    specs[who] = SeatSpec(hero=GUYONG, hp=3, hp_max=3, hand=mine)
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck_excluding(PACK, *mine) if rest else (),
        turn_seat=turn_seat,
        rule_config=RuleConfig(player_count=seats, first_seat=turn_seat),
    )


def _bingyi_activation(engine) -> bool:
    """停在顾雍结束阶段的【秉壹】「是否发动」询问上（**还没应答**）。"""
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.SKILL_ACTIVATION
        and str(engine.pending.skill_id) == BINGYI
    )


def _park_bingyi(state):
    """构造局面 → 开到顾雍结束阶段的「是否发动」询问（摸牌阶段置 0 张）。"""
    engine = Engine.from_state(state, seed=1)
    engine.set_draw_phase_count(0, 0)
    return _drive(engine, _bingyi_activation, activate=(BINGYI,))


def _declare_bingyi(engine) -> None:
    """在【秉壹】的「是否发动」上答「发动」（后续询问 / 摸牌由调用方推进）。"""
    query = engine.pending
    assert _bingyi_activation(engine), f"不在【秉壹】的发问上：{query}"
    engine.submit(next(a for a in query.legal_actions if isinstance(a, ActivateSkill)))


def _bingyi_seats(engine) -> bool:
    """停在【秉壹】的**座位多选**询问上（`SELECT_OPTIONS`；颜色全同时才会出现）。"""
    return engine.pending is not None and engine.pending.kind == QueryKind.SELECT_OPTIONS


def _turn_over(seat: int = 0):
    """回调：座位 ``seat``（顾雍）那一回合**已经走完**（回合指针过到别人）——"整段没有第二问"的观察点。

    用回合指针而不是"下一问"：摸牌阶段不提问，座位上那个人摸完两张才出第一个询问。
    """

    def stop(engine) -> bool:
        current = engine.state.turn_seat
        return current is not None and int(current) != seat

    return stop


def _reveals(engine) -> list[tuple[int, int]]:
    """记录流里的 ``card_reveal``（``(座位, 牌 id)``，按出现顺序）。"""
    return [
        (record.seat, record.card.card_id)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_reveal"
    ]


def test_bingyi_reveals_every_hand_card_once():
    """效果第 1 段：**逐张** `card_reveal` 亮出全部手牌，且只亮一次（展示不搬牌）。

    票面 §2.5 第 2 行：展示段没有第二问，走既有 `card_reveal` 通道（与【火攻】/界【清俭】
    同一条）；重入不重复亮（帧私有游标，与界【清俭】的展示段同姿态）。
    """
    mine = _bingyi_hand("red", "red")
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _turn_over())

    assert sorted(_reveals(engine)) == sorted((0, card_id) for card_id in mine), (
        f"展示段必须把手里这两张各亮一次：{_reveals(engine)}"
    )
    assert sorted(engine.state.player(0).hand) == sorted(mine), "展示不搬牌（牌仍在手里）"


def test_bingyi_each_chosen_seat_draws_one():
    """效果第 3 段（G5）：座位多选面一次交齐**全部**选中座位 ⇒ 每个各摸 1 张。

    交互面 = W1 的座位多选面（`SELECT_OPTIONS` + 非空 `option_seats`）：候选 =
    `options` 的子集，一笔动作里把选中的座位全部上行——不是"选好一个提交再继续选"。
    这里只选座位 1（**不含自己**）⇒ 只有它摸牌，顾雍自己的手牌一张不动。
    """
    mine = _bingyi_hand("red", "red")
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    assert [str(option) for option in query.options] == ["seat:0", "seat:1"], (
        "可选池 = 全部存活角色（**含自己**，Q-G2 = A），按座位序"
    )
    assert (query.min_selected, query.max_selected) == (1, 2), "至多 N 名（N = 手牌数）"
    assert any(isinstance(action, Pass) for action in query.legal_actions), (
        "「至多」⇒ 可以放弃（账本 L775 / L753）"
    )

    engine.submit(SelectOptions(choices=("seat:1",), answers_seq=query.seq))
    engine = _drive(engine, _turn_over())

    assert len(engine.state.player(1).hand) == 1, "被选中的座位摸一张"
    assert sorted(engine.state.player(0).hand) == sorted(mine), "没被选 ⇒ 顾雍不摸"
    assert [len(record.cards) for record in _drawn_batches(engine)] == [1], "逐座位各摸一张"


def _select_options_queries(engine) -> list:
    """记录流里的 ``SELECT_OPTIONS`` 询问（"整段有没有第二问"的证据）。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_OPTIONS
    ]


def test_bingyi_reveals_without_asking_when_the_colors_differ():
    """效果第 3 段（票面 §2.5 第 4 行）：颜色不全同 ⇒ **只展示**、整段不产生任何询问。

    「不发没有候选的询问」（`edge-cases.md` 总纪律 1）：条件不成立时连座位面都不出现，
    也没有人摸牌——但展示**已经发生**（牌面第 1 句无条件执行，「可以」的放弃出口在上一问）。
    """
    mine = _bingyi_hand("red", "black")
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _turn_over())

    assert sorted(_reveals(engine)) == sorted((0, card_id) for card_id in mine), (
        f"条件不成立也要展示：{_reveals(engine)}"
    )
    assert _select_options_queries(engine) == [], "颜色不全同 ⇒ 不出现座位多选面"
    assert _drawn_batches(engine) == [], "一张都不摸"
    assert sorted(engine.state.player(0).hand) == sorted(mine)


def test_bingyi_rejects_mixed_colors_even_with_the_same_category():
    """**只看颜色**（Q-G2 = A）：同为**基本牌**、但一红一黑 ⇒ 条件**不成立**。

    界顾雍移动版的牌面是「颜色**或类别**均相同」，那一支在本版**不存在**：两张都是基本牌
    （同一类别）在界版会成立、在本版**结算相反**。这条钉的正是这个分支不存在。
    """
    black = _basic_of_color("black")
    red = _basic_of_color("red", exclude=(black,))
    mine = (black, red)
    assert card_category(PACK.pack_id, PACK.deck.cards[black].type) is Category.BASIC
    assert card_category(PACK.pack_id, PACK.deck.cards[red].type) is Category.BASIC, (
        "构造：两张都是**基本牌**（同一类别）"
    )
    assert color_of_suit(PACK.deck.cards[black].suit) is not color_of_suit(
        PACK.deck.cards[red].suit
    ), "构造：一黑一红（同类别、异颜色）"

    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _turn_over())

    assert _select_options_queries(engine) == [], "本版**没有**类别那一支 ⇒ 不出现座位面"
    assert _drawn_batches(engine) == [], "同类别不算「均相同」⇒ 无人摸牌"
    assert sorted(_reveals(engine)) == sorted((0, card_id) for card_id in mine)


def _run_bingyi_turn(state, *, who: int = 0):
    """整回合跑到回合指针过到别人（**不假设**【秉壹】会问「是否发动」）。"""
    engine = Engine.from_state(state, seed=1)
    engine.set_draw_phase_count(who, 0)
    return _drive(engine, _turn_over(who), activate=(BINGYI,))


def test_bingyi_with_one_hand_card_still_qualifies():
    """1 张手牌「均相同」= **平凡成立**（`04-bingyi-uniform.md` 的 A 裁定，直接沿用）。

    ⇒ 名额 N = 1：座位面照出、区间 ``[1, 1]``（含自己），选谁谁摸一张。
    """
    mine = _bingyi_hand("red")
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    assert (query.min_selected, query.max_selected) == (1, 1), "1 张手牌 ⇒ 恰好 1 个名额"
    engine.submit(SelectOptions(choices=("seat:0",), answers_seq=query.seq))
    engine = _drive(engine, _turn_over())

    assert len(engine.state.player(0).hand) == 2, "选自己 ⇒ 自己摸一张"
    assert len(engine.state.player(1).hand) == 0, "没选别人 ⇒ 别人不摸"


def test_bingyi_is_not_offered_without_hand_cards():
    """0 张手牌 ⇒ **不给发动入口**（`02-guyong.md` Q3 = A）：连问都不问、也没有展示。"""
    engine = _run_bingyi_turn(_bingyi_state(mine=()))

    assert not _skill_asked(engine, BINGYI), "手牌为空 ⇒ 连「是否发动」都不问（零副作用）"
    assert _reveals(engine) == [], "没发动 ⇒ 一张都不亮"
    assert _select_options_queries(engine) == []


def test_bingyi_is_not_offered_in_another_players_finish_phase():
    """**不是自己的结束阶段** ⇒ 不问（阶段体帧的 ``source`` 自校；"关于我"的条件）。"""
    mine = _bingyi_hand("red", "red")
    engine = _run_bingyi_turn(_bingyi_state(mine=mine, who=1, turn_seat=0))

    assert engine.state.turn_seat == 1, "构造：回合指针已经走到顾雍（座位 1）——座位 0 的回合跑完了"
    assert not _skill_asked(engine, BINGYI), "座位 0 的结束阶段不该问座位 1 的【秉壹】"
    assert _reveals(engine) == []


def test_bingyi_asks_once_per_finish_phase():
    """G4：逐事件一次——一个结束阶段只问一次「是否发动」、只出一次座位面。"""
    mine = _bingyi_hand("red", "red")
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending
    engine.submit(SelectOptions(choices=("seat:1",), answers_seq=query.seq))
    engine = _drive(engine, _turn_over())

    assert len(_asked(engine, BINGYI)) == 1, "一个结束阶段只问一次"
    assert len(_select_options_queries(engine)) == 1, "座位面也只出一次"


def test_bingyi_seat_cap_equals_the_hand_size():
    """名额上限 N = **手牌数**（票面 §2.1「数值与次数」①）：3 张手牌 ⇒ ``max_selected == 3``。

    名额比场上人数还大是正常局面（残局里手牌 5 张三人的桌子）：上限照手牌数给，**不**按
    可选池裁剪——池只有 2 个座位，N 仍是 3。
    """
    mine = _bingyi_hand("red", "red", "red")
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    assert len(engine.state.player(0).hand) == 3, "构造：展示那一刻手牌 3 张"
    assert query.max_selected == 3, "N = 手牌数（不按可选池裁剪）"
    assert len(query.options) == 2, "可选池 = 存活座位（含自己）"


def test_bingyi_rejects_more_seats_than_the_cap():
    """超过名额上限的答复不是候选（引擎 fail-closed）：1 张手牌 ⇒ 不能一次选两个座位。"""
    mine = _bingyi_hand("red")
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    assert query.max_selected == 1
    with pytest.raises(IllegalActionError):
        engine.submit(SelectOptions(choices=("seat:0", "seat:1"), answers_seq=query.seq))


def test_bingyi_keeps_the_seat_cap_from_the_reveal():
    """G9①：N 与「颜色是否全同」在**展示那一刻**定格（写进帧私有），之后不重算。

    可达局面里"定格"与"每次现算"不可区分（选座期间没有任何路径能改顾雍的手牌），故按
    【英魂】`test_yinghun_x_is_snapshotted_from_lost_hp` 的同一姿态钉**帧私有里存着**那一个
    N，且座位面的区间读的就是它。
    """
    mine = _bingyi_hand("red", "red")
    engine = _park_bingyi(_bingyi_state(mine=mine))
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)

    frame = engine.phase_body_frame()
    assert frame is not None, "结束阶段的阶段体帧还在（不压栈的那条）"
    assert frame.data.get("bingyi_cap") == 2, "N = 展示那一刻的手牌数 = 2"
    assert frame.data.get("bingyi_uniform") is True, "颜色全同也在那一刻定格"
    assert engine.pending.max_selected == 2, "座位面的区间读同一份快照"


def test_bingyi_does_not_draw_twice_when_a_gain_hook_suspends():
    """G7：逐座位摸牌内联触发【清俭】并挂起 ⇒ 续跑不重摸、也不漏摸后面的座位。"""
    mine = _bingyi_hand("red", "red")
    state = _bingyi_state(mine=mine)
    state.player(0).granted_skills.append(QINGJIAN)
    engine = _park_bingyi(state)
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    engine.submit(SelectOptions(choices=("seat:0", "seat:1"), answers_seq=query.seq))
    engine = _drive(
        engine,
        lambda e: e.pending is not None and str(e.pending.skill_id) == QINGJIAN,
    )
    assert engine.pending is not None and str(engine.pending.skill_id) == QINGJIAN, (
        "第一座摸牌确实内联问了【清俭】（挂起点）"
    )
    engine = _drive(engine, _turn_over())

    assert _skill_asked(engine, QINGJIAN)
    assert [len(record.cards) for record in _drawn_batches(engine)] == [1, 1], (
        f"两个座位各摸一张（重入不重摸）：{_drawn_batches(engine)}"
    )
    assert len(engine.state.player(0).hand) == 3, "自己那两张 + 摸的一张"
    assert len(engine.state.player(1).hand) == 1, "另一个座位也摸到一张"


def test_bingyi_short_deck_ends_in_a_draw():
    """G10：座位摸牌那一刻两堆皆空 ⇒ 短摸即平局（既有路径；技能只把结局如实透传）。"""
    mine = _bingyi_hand("red")
    engine = _park_bingyi(_bingyi_state(mine=mine, rest=False))
    assert not engine.state.deck and not engine.state.discard, "开局两堆皆空"
    _declare_bingyi(engine)
    engine = _drive(engine, _bingyi_seats)
    query = engine.pending

    engine.submit(SelectOptions(choices=("seat:0",), answers_seq=query.seq))
    engine = _drive(engine, lambda e: e.finished)

    assert engine.finished
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result == "draw" and tail.reason == "both_decks_empty"


def test_bingyi_wording_is_its_own_and_reads_the_seat_step():
    """A4 + 逐步文案：``requirement`` / ``step_requirements`` 逐字取**本版**牌面，短 id 表未改。

    界顾雍移动版将来也会落地同名技能（`bingyi`）⇒ 本版**不得**往共享的
    ``_REQUIREMENTS`` / ``_STEP_REQUIREMENTS`` 里加一行给两版共用（会互相念错牌面）。
    逐步文案的键 = ``seat``：询问 ``skill_id`` 是 ``hero.guyong.bingyi:seat``，投影
    ``_step_requirement`` 按冒号后那段查表 ⇒ 座位多选面在提示条上显示这一句。
    """
    from sgs.skills import CATALOG, flow_for
    from sgs.skills.phase_body import _REQUIREMENTS, _STEP_REQUIREMENTS

    ours = CATALOG[BINGYI]

    assert ours.requirement == (
        "结束阶段，你可以展示所有手牌。若你的手牌颜色均相同，"
        "你令至多等同于你手牌数名角色各摸一张牌"
    )
    assert "类别" not in ours.requirement, "本版只看颜色（Q-G2 = A）"
    assert dict(ours.step_requirements) == {
        "seat": "选择令其摸一张牌的角色（可多选，选好后提交）"
    }
    assert "bingyi" not in _REQUIREMENTS, "短 id 表不加 bingyi（界版将来也会落地同名技能）"
    assert "bingyi" not in _STEP_REQUIREMENTS
    flow = flow_for(BINGYI)
    assert flow is not None and flow.name == "guyong_bingyi", (
        "流水线名另起（tests/test_ask_points.py 机械核全部模块级 flow 名两两不同）"
    )
    assert flow.steps == (), "零步：手写主体（shapes.md §2 第 9 行）"
