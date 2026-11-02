"""M6 票 17：界·吴（界孙权 / 界黄盖 / 界大乔 / 界孙尚香）。

八条技能逐条走**外部缝**（候选 / 询问 / 记录流 / 终局状态），与各族的既有测试同一姿态：

========================  ============================================================
技能                        牌面（§十九 L2121–L2127、L2146–L2151、L2159–L2165、
                            L2173–L2180）与落点
========================  ============================================================
界孙权【制衡】              "弃置任意张牌，然后摸等量的牌（若你以此法弃置了所有的手牌，
                            则多摸一张牌）"——与标准版同一条单步清单，只在提交点多摸一张
界孙权【救援】              "当其他吴势力角色于其回合内回复体力时，若其体力值不小于你，
                            该角色可以改为令你回复 1 点体力，然后其摸一张牌"——
                            **转移回复**（决定者 = 回复的吴势力角色，受益者 = 主公）
界黄盖【苦肉】              "出牌阶段限一次，弃置一张牌，失去 1 点体力"（**不摸牌**）
界黄盖【诈降】              锁定技：失去 1 点体力后摸三张；自己的出牌阶段里本阶段
                            红色【杀】无距离限制且不能被【闪】响应、【杀】次数 +1
界大乔【国色】              二选一（方块牌当【乐不思蜀】/ 弃方块并弃场上一张【乐不思蜀】），
                            "选择结束后，你摸一张牌"
界大乔【流离】              与标准版**文字相同**（同一条钩子 / 流水线）
界孙尚香【结姻】            "弃置一张手牌或将一张装备牌置入其装备区；较高的摸一张、
                            较低的回复 1 点"
界孙尚香【枭姬】            与标准版**文字相同**（同一条触发谓词 / 零步流水线）
========================  ============================================================

**两条裁定**（`06`（§5「程序化约定与偏离一手」））：【国色】"选择结束后"= 所选那一项
**完全执行完之后**才摸（选项 1 是【乐不思蜀】使用结算完）；【结姻】的"一张装备牌" = 手牌 +
装备区里的装备牌（票 23 用户裁定，推翻票 17 的"只从手牌取"）。
"""

from __future__ import annotations

from sgs.agents import HeuristicPlayer
from sgs.data import Gender, Identity, Kingdom, RuleConfig, load_content_pack
from sgs.effects import check_count, check_targets, legal_actions_for
from sgs.engine import Engine, ai_stream
from sgs.state import (
    ActivateSkill,
    FrameKind,
    MoveReason,
    Pass,
    Phase,
    QueryKind,
    SelectCards,
    SelectOption,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import (
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    run_queries,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")

ZHIHENG = "hero.jie_sunquan.zhiheng"
JIUYUAN = "hero.jie_sunquan.jiuyuan"
KUROU = "hero.jie_huanggai.kurou"
GUOSE = "hero.jie_daqiao.guose"
LIULI = "hero.jie_daqiao.liuli"
JIEYIN = "hero.jie_sunshangxiang.jieyin"
XIAOJI = "hero.jie_sunshangxiang.xiaoji"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _red_slash() -> int:
    return next(
        card_id
        for card_id in _cards("slash")
        if PACK.deck.cards[card_id].suit in ("heart", "diamond")
    )


def _black_slash() -> int:
    return next(
        card_id
        for card_id in _cards("slash")
        if PACK.deck.cards[card_id].suit in ("spade", "club")
    )


def _diamond() -> int:
    return next(
        card_id for card_id in _cards("dodge") if PACK.deck.cards[card_id].suit == "diamond"
    )


class Scripted:
    """先跑 ``handler``，返回 ``None`` 时退到最小合法应答（结束阶段 / 放弃 / 首个候选）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return _pass_or_end(query)


def _pass_or_end(query):
    if query.kind in (QueryKind.RESPOND, QueryKind.DODGE, QueryKind.PEACH):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    if query.kind == QueryKind.PLAY_PHASE:
        for action in query.legal_actions:
            if getattr(action, "kind", None) == "EndPhase":
                return action
    return query.legal_actions[0]


def _run(state, players, stop, seed: int = 3):
    """跑到 ``stop(engine)`` 成立即停；缺的座位补脚本参与者（不然 4 人局索引越界）。"""
    engine = Engine.from_state(state, seed=seed)
    participants = list(players) + [Scripted()] * (len(state.players) - len(players))
    run_queries(engine, participants, stop)
    return engine


def _phase_over(engine) -> bool:
    return engine.finished or engine.state.phase in (Phase.DISCARD, Phase.FINISH)


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


def _activated(engine, skill_id: str) -> bool:
    return any(
        record.kind == "action"
        and record.accepted
        and isinstance(record.action, ActivateSkill)
        and record.action.skill_id == skill_id
        for record in engine.records
    )


def _ran(engine, timing: Timing) -> bool:
    return any(
        record.kind == "timing" and record.timing is timing for record in engine.records
    )


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _skill_offers(state, skill_id: str, seat: int = 0) -> list[ActivateSkill]:
    return [
        action
        for action in legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, ActivateSkill) and action.skill_id == skill_id
    ]


# ---------------------------------------------------------------------------
# ① 界孙权【制衡】：弃光所有手牌多摸一张
# ---------------------------------------------------------------------------


def _hand_at_play_phase(state) -> tuple[int, ...]:
    """跑到第一条出牌阶段询问，读此刻的手牌（不提交任何动作）。"""
    engine = Engine.from_state(state, seed=3)
    run_queries(
        engine,
        [Scripted(), Scripted()],
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )
    return tuple(engine.state.player(0).hand)


def _zhiheng_state(*, hero: str, hand: tuple[int, ...]):
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero=hero), SeatSpec()],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
    )


def _zhiheng_engine(*, hero: str, hand: tuple[int, ...], picks: tuple[int, ...]):
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero=hero), SeatSpec()],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
    )

    def confirm(query):
        if query.kind == QueryKind.PLAY_PHASE and f"hero.{hero}.zhiheng" in getattr(
            query, "preactivations", {}
        ):
            return ActivateSkill(
                skill_id=f"hero.{hero}.zhiheng", picks=picks, answers_seq=query.seq
            )
        return None

    return _run(state, [Scripted(confirm), Scripted()], _phase_over)


def test_jie_zhiheng_draws_one_extra_when_every_hand_card_is_discarded():
    """正例：弃光**当前全部手牌**（含摸牌阶段摸到的两张）⇒ 摸等量 + 1。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    all_hand = _hand_at_play_phase(_zhiheng_state(hero="jie_sunquan", hand=(first, second)))
    assert len(all_hand) == 4, "摸牌阶段后手牌 = 起始 2 + 摸 2"

    engine = _zhiheng_engine(hero="jie_sunquan", hand=(first, second), picks=all_hand)

    assert _activated(engine, ZHIHENG)
    assert len(engine.state.player(0).hand) == 5, "弃 4 张摸 5 张（多摸一张）"
    assert set(all_hand) <= set(engine.state.discard)


def test_jie_zhiheng_without_discarding_all_hand_cards_draws_the_same_count():
    """反例：只弃一部分手牌 ⇒ 摸等量，没有额外一张。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    picks = _hand_at_play_phase(_zhiheng_state(hero="jie_sunquan", hand=(first, second)))[:2]

    engine = _zhiheng_engine(hero="jie_sunquan", hand=(first, second), picks=picks)

    assert _activated(engine, ZHIHENG)
    assert len(engine.state.player(0).hand) == 4, "起始 4 张 − 弃 2 + 摸 2 = 4"


def test_the_standard_sunquan_does_not_get_the_extra_card():
    """反例（归属）：同一组"弃光所有手牌"在标准孙权手里只摸等量。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    all_hand = _hand_at_play_phase(_zhiheng_state(hero="sunquan", hand=(first, second)))

    engine = _zhiheng_engine(hero="sunquan", hand=(first, second), picks=all_hand)

    assert _activated(engine, "hero.sunquan.zhiheng")
    assert len(engine.state.player(0).hand) == 4, "标准版：弃 4 摸 4"


# ---------------------------------------------------------------------------
# ② 界黄盖【苦肉】：限一次 + 弃一张牌 + 失去 1 点体力（不摸牌）
# ---------------------------------------------------------------------------


def _jie_kurou_engine(*, hand: tuple[int, ...], equip: tuple[int, ...] = ()):
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, equip=equip, hero="jie_huanggai"), SeatSpec()],
        deck=deck_excluding(PACK, *hand, *equip),
        turn_seat=0,
    )
    used = {"count": 0}

    def choose(query):
        if query.kind == QueryKind.PLAY_PHASE and used["count"] == 0:
            action = _find(
                query, lambda a: isinstance(a, ActivateSkill) and a.skill_id == KUROU
            )
            if action is not None:
                used["count"] += 1
                return action
        if query.kind == QueryKind.SELECT_CARDS:
            return SelectCards(picks=(0,), answers_seq=query.seq)
        return None

    return _run(state, [Scripted(choose), Scripted()], _phase_over), used


def test_jie_kurou_discards_one_card_and_loses_one_hp_without_drawing():
    """正例：弃一张牌、失去 1 点体力；【苦肉】本身不摸牌（摸三张的是【诈降】）。"""
    card = _cards("lightning")[0]
    engine, used = _jie_kurou_engine(hand=(card,))

    assert used["count"] == 1, "脚本该发动一次界【苦肉】"
    assert _activated(engine, KUROU)
    assert engine.state.player(0).hp == 3, "失去 1 点体力"
    assert _moves_of(engine, card) == [("hand", "discard", "DISCARD")]
    # 起始 1 + 摸牌阶段 2 − 弃 1 + 【诈降】摸 3 = 5（若【苦肉】也摸 2 会是 7）。
    assert len(engine.state.player(0).hand) == 5


def test_jie_kurou_is_limited_to_once_per_play_phase():
    """反例（次数）：界【苦肉】"出牌阶段限一次"——同一阶段不再产出候选。"""
    first, second = _cards("lightning")[0], _cards("harvest")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), hero="jie_huanggai"), SeatSpec()],
        deck=deck_excluding(PACK, first, second),
        turn_seat=0,
    )
    assert _skill_offers(state, KUROU), "第一次可发动"

    engine, used = _jie_kurou_engine(hand=(first, second))

    assert used["count"] == 1
    play_queries = [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.PLAY_PHASE.value
    ]
    assert play_queries, "本阶段应当问过出牌阶段"
    assert all(
        not any(
            isinstance(action, ActivateSkill) and action.skill_id == KUROU
            for action in query.legal_actions
        )
        for query in play_queries[1:]
    ), "发动过之后不再产出界【苦肉】候选"


def test_the_standard_huanggai_kurou_is_still_repeatable_without_a_cost():
    """反例（对照）：标准【苦肉】不弃牌、不限次数、摸两张。"""
    card = _cards("lightning")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,), hero="huanggai"), SeatSpec()],
        deck=deck_excluding(PACK, card),
        turn_seat=0,
    )
    assert _skill_offers(state, "hero.huanggai.kurou")


# ---------------------------------------------------------------------------
# ③ 界黄盖【诈降】：失去体力后摸三张 + 本阶段三条【杀】加成
# ---------------------------------------------------------------------------


def test_zhaxiang_does_not_trigger_on_damage():
    """反例（用户 2026-10-31 实机报障）：**受到伤害不算「失去体力」**，不问不摸。

    伤害链里确实压了一次 ``LIFE_DECREASE`` 子帧（L8），但官方把"受到伤害"与"失去体力"当
    两件事：【诈降】只认后者（【苦肉】这类直接扣减）。旧实现只按时机挂，于是"被打 1 点"
    也摸三张——用户实机复现的原样。
    """
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_huanggai"), SeatSpec()],
        deck=deck_excluding(PACK, slash),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=5)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 1,
            "targets": (0,),
            "card": state.card(slash),
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 0,
            "channel": state.card(slash),
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
            "damage_source": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    run_queries(engine, [Scripted(), Scripted()], lambda e: not e._move_pending and not e.stack)

    assert engine.state.player(0).hp == 3, "伤害照常扣体力"
    assert len(engine.state.player(0).hand) == 0, "受到伤害**不**触发【诈降】"
    assert not engine.state.player(0).zhaxiang_active, (
        "别人回合里的体力损失不开启「本阶段」效果"
    )


def test_zhaxiang_draws_three_after_a_real_hp_loss():
    """正例（锁定）：【苦肉】那种**失去体力**照常摸三张。"""
    discard = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_huanggai", hand=(discard,)), SeatSpec()],
        deck=deck_excluding(PACK, discard),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=11)
    used = {"count": 0, "before": 0}

    def drive(query):
        if query.kind == QueryKind.PLAY_PHASE and used["count"] == 0:
            action = _find(
                query, lambda a: isinstance(a, ActivateSkill) and a.skill_id == KUROU
            )
            if action is not None:
                used["count"] += 1
                used["before"] = len(engine.state.player(0).hand)
                return action
        if query.kind == QueryKind.SELECT_CARDS:
            return SelectCards(picks=(0,), answers_seq=query.seq)
        return None

    def ready(e):
        return e.state.player(0).zhaxiang_active

    run_queries(engine, [Scripted(drive), Scripted()], ready)
    assert engine.state.player(0).hp == 3, "界苦肉：弃一张、失去 1 点体力"
    assert len(engine.state.player(0).hand) == used["before"] - 1 + 3, (
        "失去体力后摸三张（【诈降】）：弃 1 张 + 摸 3 张"
    )


def _zhaxiang_phase_state(*, hand, deck):
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=hand, hero="jie_huanggai"),
            SeatSpec(hand=(_cards("dodge")[0],)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck,
        turn_seat=0,
    )


def _use_kurou_once(engine):
    """跑到"苦肉已发动、诈降本阶段生效、出牌阶段询问再次出现"那一刻。"""
    used = {"count": 0}

    def drive(query):
        if query.kind == QueryKind.PLAY_PHASE and used["count"] == 0:
            action = _find(
                query, lambda a: isinstance(a, ActivateSkill) and a.skill_id == KUROU
            )
            if action is not None:
                used["count"] += 1
                return action
        if query.kind == QueryKind.SELECT_CARDS:
            return SelectCards(picks=(0,), answers_seq=query.seq)
        return None

    def ready(e):
        return (
            e.state.player(0).zhaxiang_active
            and e.pending is not None
            and e.pending.kind == QueryKind.PLAY_PHASE.value
        )

    run_queries(engine, [Scripted(drive), Scripted(), Scripted(), Scripted()], ready)


def test_zhaxiang_opens_the_slash_bonuses_in_its_own_play_phase():
    """正例：本阶段红【杀】无距离限制 / 不可被【闪】响应 / 【杀】次数上限 +1。"""
    red = _red_slash()
    junk = _cards("lightning")[0]
    dodge = _cards("dodge")[0]
    state = _zhaxiang_phase_state(hand=(junk, red), deck=deck_excluding(PACK, junk, red, dodge))
    engine = Engine.from_state(state, seed=5)

    _use_kurou_once(engine)

    live = engine.state
    assert live.player(0).zhaxiang_active
    # 座 2 与座 0 距离 2（4 人局）：本阶段之前不在攻击范围，之后进红色【杀】的目标池。
    assert 2 in check_targets(live, 0, live.card(red)), "红色【杀】无距离限制"
    assert 2 not in check_targets(live, 0, live.card(_black_slash())), "只豁免红色【杀】"

    use = _find(
        engine.pending,
        lambda a: isinstance(a, UseCard) and a.card.card_id == red and a.targets == (2,),
    )
    assert use is not None, "无距离限制的红色【杀】应当出现在本阶段候选里"
    engine.submit(use)
    dodge_queries = []
    while not _phase_over(engine):
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        if query.kind == QueryKind.DODGE:
            dodge_queries.append(query)
        engine.submit(_pass_or_end(query))

    assert dodge_queries == [], "红色【杀】不可被【闪】响应"
    assert engine.state.player(2).hp == 3, "无距离限制的红色【杀】真的命中"


def test_zhaxiang_phase_scope_is_cleared_at_the_end_of_the_play_phase():
    """验收：本阶段的作用域在**出牌阶段结束**清干净（不残留到下一个阶段）。"""
    card = _cards("lightning")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,), hero="jie_huanggai"), SeatSpec()],
        deck=deck_excluding(PACK, card),
        turn_seat=0,
    )
    used = {"count": 0}

    def drive(query):
        if query.kind == QueryKind.PLAY_PHASE and used["count"] == 0:
            action = _find(
                query, lambda a: isinstance(a, ActivateSkill) and a.skill_id == KUROU
            )
            if action is not None:
                used["count"] += 1
                return action
        if query.kind == QueryKind.SELECT_CARDS:
            return SelectCards(picks=(0,), answers_seq=query.seq)
        return None

    engine = _run(state, [Scripted(drive), Scripted()], lambda e: e.state.phase is Phase.DISCARD)

    assert _ran(engine, Timing.PHASE_PLAY_END)
    assert not engine.state.player(0).zhaxiang_active, (
        "出牌阶段一结束，本阶段的三条加成必须清干净"
    )


def test_zhaxiang_count_bonus_lets_a_second_slash_through():
    """正例（次数）：本阶段【杀】上限 +1（**不**限定红色）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_huanggai"), SeatSpec()],
        turn_seat=0,
        phase=Phase.PLAY,
    )
    slash = _black_slash()
    state.player(0).card_use_counts["slash"] = 1
    assert check_count(state, 0, state.card(slash)), "本阶段之前：上限 1、已用 1 ⇒ 不能再出"
    state.player(0).zhaxiang_active = True

    assert not check_count(state, 0, state.card(slash)), "本阶段：上限 2、已用 1 ⇒ 还能出"


# ---------------------------------------------------------------------------
# ④ 界大乔【国色】:二选一 + 选择结束后摸一张
# ---------------------------------------------------------------------------


def _guose_setup(*, judge=(), target_hand=()):
    diamond = _diamond()
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(diamond,), hero="jie_daqiao"),
            SeatSpec(hand=target_hand, judge=judge),
        ],
        deck=deck_excluding(PACK, diamond, *judge, *target_hand),
        turn_seat=0,
    )
    return diamond, state


def _guose_driver(option, *, watch=None):
    used = {"count": 0}

    def drive(query):
        if query.kind == QueryKind.PLAY_PHASE and used["count"] == 0:
            action = _find(
                query, lambda a: isinstance(a, ActivateSkill) and a.skill_id == GUOSE
            )
            if action is not None:
                used["count"] += 1
                return action
        if query.kind == QueryKind.SELECT_OPTION:
            if query.skill_id.endswith(":option"):
                return SelectOption(choice=option, answers_seq=query.seq)
            return SelectOption(choice="seat:1", answers_seq=query.seq)
        if query.kind == QueryKind.SELECT_CARDS:
            return SelectCards(picks=(0,), answers_seq=query.seq)
        if watch is not None:
            return watch(query)
        return None

    return drive, used


def test_jie_guose_use_branch_places_a_transformed_indulgence_and_draws_one():
    """正例（选项 1）：方块牌当【乐不思蜀】真进判定区；之后摸一张。"""
    diamond, state = _guose_setup()
    drive, used = _guose_driver("guose_use_indulgence")
    engine = _run(state, [Scripted(drive), Scripted()], _phase_over)

    assert used["count"] == 1
    assert _activated(engine, GUOSE)
    assert engine.state.player(1).judge == [diamond], "来源方块牌进目标判定区"
    assert engine.state.virtual_judge.get(diamond) is not None, "判定区里代表虚拟【乐不思蜀】"
    assert len(engine.state.player(0).hand) == 3, "起始 1 + 摸 2 − 用 1 + 国色摸 1"


def test_jie_guose_draws_only_after_the_chosen_option_resolves():
    """裁定（`06`（§5「程序化约定与偏离一手」））：选项 1 的"选择结束后摸一张"在【乐不思蜀】使用**结算完**之后。

    观测点 = 无懈窗口：窗口开着时那张国色摸的牌还不在手里（3 张起始，用掉一张 = 2；
    结算完才到 3）。
    """
    diamond = _diamond()
    nullify = _cards("nullify")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(diamond,), hero="jie_daqiao"), SeatSpec(hand=(nullify,))],
        deck=deck_excluding(PACK, diamond, nullify),
        turn_seat=0,
    )
    drive, _ = _guose_driver("guose_use_indulgence")
    engine = Engine.from_state(state, seed=5)
    run_queries(engine, [Scripted(drive), Scripted()], _phase_over)

    use_pops = [
        record.seq
        for record in engine.records
        if record.kind == "frame"
        and record.op == "pop"
        and record.frame_kind is FrameKind.USE
    ]
    draw_seqs = [
        record.seq
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone.zone is Zone.HAND
        and record.to_zone.seat == 0
        and record.reason is MoveReason.DRAW
        and len(record.cards) == 1
    ]
    judge_puts = [
        record.seq
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone.zone is Zone.JUDGE
    ]
    assert use_pops and draw_seqs and judge_puts
    assert draw_seqs[-1] > use_pops[-1], "国色的摸一张排在【乐不思蜀】使用结算完（USE 帧出栈）之后"
    assert draw_seqs[-1] > judge_puts[-1]
    assert len(engine.state.player(0).hand) == 3, "结算完之后摸一张"


def test_jie_guose_discard_branch_discards_a_diamond_and_a_field_indulgence():
    """正例（选项 2）：弃一张方块牌 + 弃场上一张【乐不思蜀】，然后摸一张。"""
    indulgence = _cards("indulgence")[0]
    diamond, state = _guose_setup(judge=(indulgence,))
    drive, used = _guose_driver("guose_discard_indulgence")
    engine = _run(state, [Scripted(drive), Scripted()], _phase_over)

    assert used["count"] == 1
    assert _activated(engine, GUOSE)
    assert diamond in engine.state.discard
    assert indulgence in engine.state.discard
    assert engine.state.player(1).judge == []
    assert ("judge", "discard", "DISCARD") in _moves_of(engine, indulgence)
    assert len(engine.state.player(0).hand) == 3, "起始 1 + 摸 2 − 弃 1 + 摸 1"


def test_jie_guose_does_not_offer_option_two_without_a_field_indulgence():
    """反例：场上没有【乐不思蜀】⇒ 选项 2 不出现（仍可选项 1）。"""
    diamond, state = _guose_setup()
    assert _skill_offers(state, GUOSE)

    options: list[str] = []
    drive, _ = _guose_driver("guose_use_indulgence")
    engine = Engine.from_state(state, seed=5)

    def inspect(query):
        if query.kind == QueryKind.SELECT_OPTION and query.skill_id.endswith(":option"):
            options.extend(query.options)
        return drive(query)

    run_queries(engine, [Scripted(inspect), Scripted()], _phase_over)
    assert options == ["guose_use_indulgence"], "选项 2 不出现"


def test_jie_guose_is_not_offered_without_a_diamond_card():
    """反例（付不起）：没有方块牌 ⇒ 两个选项都付不起 ⇒ 一个候选都不造。"""
    black = _black_slash()
    indulgence = _cards("indulgence")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(black,), hero="jie_daqiao"), SeatSpec(judge=(indulgence,))],
        deck=deck_excluding(PACK, black, indulgence),
        turn_seat=0,
    )
    assert not _skill_offers(state, GUOSE)


def test_the_standard_daqiao_guose_remains_a_plain_conversion():
    """反例（归属）：标准【国色】仍是"方块牌当【乐不思蜀】"的普通转化，没有二选一 / 摸牌。"""
    from sgs.skills import CATALOG, flow_for

    assert flow_for("hero.daqiao.guose") is None, "标准版没有技能流水线"
    assert CATALOG["hero.daqiao.guose"].virtual_type_ids == ("indulgence",)
    assert flow_for(GUOSE) is not None, "界版是主动技（有流水线）"


# ---------------------------------------------------------------------------
# ⑤ 界大乔【流离】：与标准版逐字相同（同一份声明）
# ---------------------------------------------------------------------------


def test_jie_liuli_transfers_the_slash_like_the_standard_version():
    """正例（复用）：界大乔成为【杀】的目标 ⇒ 弃一张牌、转移给攻击范围内的另一名角色。"""
    slash = _cards("slash")[0]
    paid = _cards("lightning")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,)),
            SeatSpec(hand=(paid,), hero="jie_daqiao"),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, paid, slash),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    seen = {"activation": 0, "target": []}

    def drive(query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == LIULI:
            seen["activation"] += 1
            return _find(query, lambda a: isinstance(a, ActivateSkill))
        if query.kind == QueryKind.SELECT_CARDS:
            return SelectCards(picks=(0,), answers_seq=query.seq)
        if query.kind == QueryKind.SELECT_OPTION:
            seen["target"].append(query.options)
            return SelectOption(choice="seat:2", answers_seq=query.seq)
        return None

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == slash:
                    return action
        return None

    run_queries(
        engine,
        [Scripted(attacker), Scripted(drive), Scripted()],
        lambda e: any(
            r.kind == "delta" and r.delta_type == "hp_change" and r.seat == 2
            for r in e.records
        ),
    )

    assert seen["activation"] == 1, "成为【杀】目标时问一次「是否发动」"
    assert seen["target"] == [("seat:2",)]
    assert paid in engine.state.discard, "弃一张牌是成本"
    assert engine.state.player(1).hp == 4, "界大乔不再结算此【杀】"
    assert engine.state.player(2).hp == 3, "被转移的目标结算此【杀】"


def test_jie_liuli_shares_the_standard_flow():
    """反例（归属）：两份声明各有各的技能 id，但**同一条**钩子流水线（文字相同）。"""
    from sgs.skills import CATALOG

    assert "hero.daqiao.liuli" in CATALOG and LIULI in CATALOG
    assert (
        CATALOG[LIULI].hooks[0].flow is CATALOG["hero.daqiao.liuli"].hooks[0].flow
    ), "同一条流水线（文字相同，行为不分叉）"


# ---------------------------------------------------------------------------
# ⑥ 界孙尚香【结姻】：一张手牌 或 一张装备牌（手牌 / 装备区）；较高摸、较低回
# ---------------------------------------------------------------------------


def _jie_jieyin_engine(
    *,
    hand,
    equip=(),
    target_hand=(),
    target_equip=(),
    owner_hp=4,
    target_hp=3,
    option,
    card=None,
):
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=owner_hp, hp_max=4, hand=hand, equip=equip, hero="jie_sunshangxiang"),
            SeatSpec(
                hp=target_hp,
                hp_max=4,
                gender=Gender.MALE,
                hand=target_hand,
                equip=target_equip,
            ),
        ],
        deck=deck_excluding(PACK, *hand, *equip, *target_hand, *target_equip),
        turn_seat=0,
    )
    used = {"count": 0}

    def drive(query):
        if query.kind == QueryKind.PLAY_PHASE and used["count"] == 0:
            action = _find(
                query, lambda a: isinstance(a, ActivateSkill) and a.skill_id == JIEYIN
            )
            if action is not None:
                used["count"] += 1
                return action
        if query.kind == QueryKind.SELECT_OPTION:
            if query.skill_id.endswith(":option"):
                return SelectOption(choice=option, answers_seq=query.seq)
            return SelectOption(choice="seat:1", answers_seq=query.seq)
        if query.kind == QueryKind.SELECT_CARDS:
            # ``card`` 给定时按牌 id 挑那一个槽（装备区来源的用例要挑第二张）；
            # 不给就仍取第一个槽（既有用例的口径）。
            pick = 0
            if card is not None:
                for index, slot in enumerate(query.slots):
                    if slot.card is not None and slot.card.card_id == card:
                        pick = index
            return SelectCards(picks=(pick,), answers_seq=query.seq)
        return None

    return _run(state, [Scripted(drive), Scripted()], _phase_over), used


def test_jie_jieyin_discard_path_recovers_for_the_lower_and_draws_for_the_higher():
    """正例（弃手牌）：界孙尚香 2 血、目标 3 血 ⇒ 自己（较低）回复 1，目标（较高）摸一张。"""
    card = _cards("lightning")[0]
    engine, used = _jie_jieyin_engine(
        hand=(card,), owner_hp=2, target_hp=3, option="jieyin_discard_hand"
    )

    assert used["count"] == 1
    assert _activated(engine, JIEYIN)
    assert card in engine.state.discard
    assert engine.state.player(0).hp == 3, "较低的角色（自己）回复 1 点"
    # 目标座位的手牌：摸牌阶段是他人的回合、不摸；只有"较高者摸一张"这一张。
    assert len(engine.state.player(1).hand) == 1


def test_jie_jieyin_equipment_path_puts_a_hand_equipment_into_an_empty_slot():
    """正例（装备路径）：手牌里的装备牌置入**目标装备区的空槽**（PUT，不顶替任何牌）。"""
    new_horse = _cards("chitu")[0]
    armor = _cards("eight_diagrams")[0]
    engine, used = _jie_jieyin_engine(
        hand=(new_horse,),
        target_equip=(armor,),
        owner_hp=2,
        target_hp=3,
        option="jieyin_place_equipment",
    )

    assert used["count"] == 1
    assert new_horse in engine.state.player(1).equip, "置入的是目标（男性）的装备区"
    assert armor in engine.state.player(1).equip, "别的槽位原样保留"
    assert ("hand", "equip", "PUT") in _moves_of(engine, new_horse)
    assert not any(
        entry[1] == "discard" for entry in _moves_of(engine, armor)
    ), "「置入」不替换任何已有装备"
    assert engine.state.player(0).hp == 3, "较低（自己 2 血）回复 1 点"
    assert len(engine.state.player(1).hand) == 1, "较高（目标 3 血）摸一张"


def test_jie_jieyin_cannot_place_into_an_occupied_slot():
    """反例（用户裁定）：目标同槽已有牌 ⇒ **这张牌不是合法成本**，该分支因此不出现。

    牌面写的是"**置入**"（不是"替换"）⇒ 目标装备区没有空槽时，"置入"这条分支的候选为空；
    手里只剩这张装备牌时，连选项都不给（:func:`_jie_jieyin_options` 按目标现算）。
    两只马同在进攻马槽，故目标是"同槽已有牌"的局面。
    """
    from sgs.skills.active import _jie_jieyin_options

    horse, taken = _cards("chitu")[0], _cards("dawan")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(horse,), hero="jie_sunshangxiang"),
            SeatSpec(hp=4, equip=(taken,), gender=Gender.MALE),
        ],
        deck=deck_excluding(PACK, horse, taken),
        turn_seat=0,
    )

    assert _jie_jieyin_options(state, 0, 1) == ("jieyin_discard_hand",), (
        "目标进攻马槽已被占 ⇒「置入」不可选（不能替换已有装备）"
    )


def test_jie_jieyin_places_an_already_equipped_horse_into_the_targets_free_slot():
    """装备区来源的正例（用户裁定）：自己**已装备**的牌也能置入，只要目标那个槽空着。"""
    filler = _cards("lightning")[0]
    horse = _cards("chitu")[0]
    armor = _cards("renwang_shield")[0]
    engine, used = _jie_jieyin_engine(
        hand=(filler,),
        equip=(horse,),
        target_equip=(armor,),
        owner_hp=2,
        target_hp=3,
        option="jieyin_place_equipment",
        card=horse,
    )

    assert used["count"] == 1
    assert horse in engine.state.player(1).equip, "自己装备区那张进了目标装备区"
    assert not engine.state.player(0).equip, "自己的装备区空了出来"
    assert armor in engine.state.player(1).equip, "目标别的槽位原样保留"
    assert any(
        "xiaoji" in str(getattr(record.query, "skill_id", ""))
        for record in engine.records
        if record.kind == "query"
    ), "把已装备的牌送出去 = 失去装备区里的一张牌 ⇒ 她自己的【枭姬】照常问一次"


def test_jie_jieyin_equal_hp_runs_neither_effect():
    """反例（裁定）：体力值相同 ⇒ 两个角色都不是"较高 / 较低"，两种效果都不执行。"""
    card = _cards("lightning")[0]
    engine, used = _jie_jieyin_engine(
        hand=(card,), owner_hp=4, target_hp=4, option="jieyin_discard_hand"
    )

    assert used["count"] == 1
    assert card in engine.state.discard, "成本照付"
    assert engine.state.player(0).hp == 4, "自己满体力、未回复"
    assert len(engine.state.player(1).hand) == 0, "目标没有摸牌"


def test_jie_jieyin_target_may_be_healthy_and_the_equipment_may_come_from_the_equip_zone():
    """目标池 + 区域：健康的男性是合法目标；「一张装备牌」**含装备区**（用户裁定，票 23）。

    牌面只写「一张**装备牌**」、没写「装备区里的牌」，按默认读法（一）③ ⇒ 手牌 + 装备区
    里的装备牌都算。旧实现只从手牌取（票 17 的技能级裁定），票 23 按用户裁定改正。
    """
    from sgs.skills.active import _jie_jieyin_options, _jie_jieyin_targets

    card = _cards("lightning")[0]
    horse = _cards("chitu")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(card,), equip=(horse,), hero="jie_sunshangxiang"),
            SeatSpec(hp=4, hp_max=4, gender=Gender.MALE),
        ],
        deck=deck_excluding(PACK, card, horse),
        turn_seat=0,
    )

    assert _jie_jieyin_targets(state, None, 0, ()) == (1,), "满体力的男性也是合法目标"
    assert _jie_jieyin_options(state, 0, 1) == (
        "jieyin_discard_hand",
        "jieyin_place_equipment",
    ), "装备区里那张也是「一张装备牌」⇒ 装备路径可用（目标坐骑槽空着）"


def test_jie_jieyin_target_pool_leaves_out_a_seat_with_no_way_through():
    """票 01：座位池只发「第 2 步至少有一个选项」的人 —— 死座位不进池。

    场景 = 票 18 finding 的实测局面：发动者手牌 0、装备区里只有武器（麒麟弓）与进攻马
    （大宛）；座位 1 的武器槽（雌雄双股剑）与进攻马槽（赤兔）**都被占** ⇒ 弃手牌没手牌、
    置装备没有空槽，两条分支都走不通。这种人不能发给参与者：AI 按关系选中它，
    ``_flow_step_option`` 见空闭集取消整条流水线，而取消不落 ``_USED`` ⇒ 同一出牌阶段
    无限重来。座位 2 的槽位空着 ⇒ 他在池里。
    """
    from sgs.skills.active import _jie_jieyin_options, _jie_jieyin_targets

    bow, horse = _cards("qilin_bow")[0], _cards("dawan")[0]
    taken_bow, taken_horse = _cards("double_sword")[0], _cards("chitu")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(), equip=(bow, horse), hero="jie_sunshangxiang"),
            SeatSpec(gender=Gender.MALE, equip=(taken_bow, taken_horse)),
            SeatSpec(gender=Gender.MALE),
        ],
        deck=deck_excluding(PACK, bow, horse, taken_bow, taken_horse),
        turn_seat=0,
    )

    assert _jie_jieyin_options(state, 0, 1) == (), "座位 1 两条分支都走不通"
    assert _jie_jieyin_targets(state, None, 0, ()) == (2,), (
        "第 2 步没有选项的座位不发出去；有空槽的男性照常在池里"
    )


def test_the_stuck_jieyin_seed_finishes_within_a_bounded_run():
    """票 01 回归（端到端）：卡死种子的一局能自然终局。

    修复前探针跑到 2 万步仍未终局（票 18 的读数）：座位 4 的界孙尚香在同一出牌阶段反复
    「发动【结姻】→ 选死座位 → 空闭集取消」（取消零副作用、不落 ``_USED``）。
    ``run_queries`` 的 ``limit`` 就是这条回归的步数上限。

    **种子随池变**：本回归是"某个真种子真的走到那条死路"，故每次默认将池换掉配将面都得
    重新选种。历史上换过两次：神吕蒙移动版进池（57 → 58）⇒ ``5667218070898781`` 重测为
    **717 次决策 / 6 324 次推进迭代**；火包移动版典韦进池（63 → 64）⇒ 原种子不再走到
    界【结姻】的候选，改取 ``100``（**396 次决策 / 3 138 次推进迭代**）。上限取 **12 000**
    ——远低于修复前的 2 万步，真回退了会**断言失败**而不是把 CI 挂死。
    """
    seed = 100
    engine = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=5),
        pack=PACK,
        hero_pool=(),
    )
    players = [HeuristicPlayer(ai_stream(seed, seat)) for seat in range(5)]

    run_queries(engine, players, lambda e: e.finished, limit=12_000)

    assert engine.finished, "步数上限内自然终局（实测 396 次决策）"
    assert any(
        record.kind == "query"
        and JIEYIN in (getattr(record.query, "preactivations", None) or {})
        for record in engine.records
    ), "该种子必须真的走到界【结姻】的候选，否则它不再覆盖本回归（需重新选种）"


# ---------------------------------------------------------------------------
# ⑦ 界孙尚香【枭姬】：与标准版逐字相同（同一条触发谓词）
# ---------------------------------------------------------------------------


def test_jie_xiaoji_draws_two_after_losing_equipment():
    """正例（复用）：界孙尚香失去装备区里的牌 ⇒ 询问并摸两张。"""
    weapon = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(weapon,), hero="jie_sunshangxiang"), SeatSpec()],
        deck=deck_excluding(PACK, weapon),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    engine.move_in_frame(
        None,
        engine.state.refs((weapon,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=0,
        actor=0,
    )

    def drive(query):
        if query.kind == QueryKind.SKILL_ACTIVATION:
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    run_queries(
        engine,
        [Scripted(drive), Scripted()],
        lambda e: not e._move_pending and len(e.state.player(0).hand) >= 2,
    )

    assert _activated(engine, XIAOJI)
    assert len(engine.state.player(0).hand) == 2, "摸两张"


def test_jie_xiaoji_does_not_fire_when_another_seat_loses_equipment():
    """反例（归属）：别人掉装备不触发界【枭姬】。"""
    weapon = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_sunshangxiang"), SeatSpec(equip=(weapon,))],
        deck=deck_excluding(PACK, weapon),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    engine.move_in_frame(
        None,
        engine.state.refs((weapon,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=1,
        actor=1,
    )
    run_queries(engine, [Scripted(), Scripted()], lambda e: not e._move_pending)

    assert not _activated(engine, XIAOJI)
    assert engine.state.player(0).hand == []


# ---------------------------------------------------------------------------
# ⑧ 界孙权【救援】：回复事件上的可选用应答（决定者 = 回复的吴势力角色）
# ---------------------------------------------------------------------------


def _jiuyuan_state(
    *, actor_kingdom=Kingdom.WU, actor_hp=2, lord_hp=2, lord_identity=Identity.LORD
):
    peach = _cards("peach")[0]
    return peach, build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hp=actor_hp,
                hp_max=4,
                hand=(peach,),
                hero="jie_huanggai",
                kingdom=actor_kingdom,
            ),
            SeatSpec(
                hp=lord_hp,
                hp_max=4,
                hero="jie_sunquan",
                identity=lord_identity,
                kingdom=Kingdom.WU,
            ),
        ],
        deck=deck_excluding(PACK, peach),
        turn_seat=0,
    )


def _run_jiuyuan(state, *, activate: bool = True):
    peach = _cards("peach")[0]
    asked: list[tuple[int, str]] = []

    def drive(query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == peach:
                    return action
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIUYUAN:
            asked.append((query.actor, query.skill_id))
            if activate:
                return _find(query, lambda a: isinstance(a, ActivateSkill))
            return _find(query, lambda a: isinstance(a, Pass))
        return None

    return _run(state, [Scripted(drive), Scripted()], _phase_over), asked


def test_jie_jiuyuan_redirects_the_recovery_to_the_lord_and_lets_the_actor_draw():
    """正例：吴势力角色在**其回合**回复体力 ⇒ 问他自己；发动后主公回复 1、他摸一张。"""
    _, state = _jiuyuan_state()
    engine, asked = _run_jiuyuan(state)

    assert asked == [(0, JIUYUAN)], "询问发给正在回复的那个吴势力角色（不是主公）"
    assert engine.state.player(0).hp == 2, "原回复被替换：他自己没有回复"
    assert engine.state.player(1).hp == 3, "主公回复 1 点"
    assert len(engine.state.player(0).hand) == 3, "然后其摸一张（起始 1 + 摸 2 − 用桃 1 + 1）"
    assert not [
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.RECOVER_AFTER
        and record.actor == 0
    ], "被替换的目标不再发 RECOVER_AFTER"


def test_jie_jiuyuan_declined_keeps_the_normal_recovery():
    """反例（可以）：他不发动 ⇒ 照常回复 1 点、主公不动、没有摸牌。"""
    _, state = _jiuyuan_state()
    engine, asked = _run_jiuyuan(state, activate=False)

    assert asked == [(0, JIUYUAN)]
    assert engine.state.player(0).hp == 3, "照常回复"
    assert engine.state.player(1).hp == 2, "主公不动"
    assert len(engine.state.player(0).hand) == 2, "没有那一张"


def test_jie_jiuyuan_is_silent_for_non_wu_or_low_hp_recoveries():
    """反例（触发条件）：非吴势力 / 体力低于主公 / 不是主公 ⇒ 一次都不问。"""
    _, non_wu = _jiuyuan_state(actor_kingdom=Kingdom.SHU)
    _, low_hp = _jiuyuan_state(actor_hp=1, lord_hp=3)
    _, no_lord = _jiuyuan_state(lord_identity=None)

    assert _run_jiuyuan(non_wu)[1] == []
    assert _run_jiuyuan(low_hp)[1] == []
    assert _run_jiuyuan(no_lord)[1] == [], "非主公的界孙权没有【救援】实例"


def test_zhaxiang_triggers_three_times_on_a_three_point_life_loss():
    """「失去 **1 点**体力后」⇒ 逐点：一次失去 3 点 ⇒ 摸三张 **×3**（票 04）。

    载体是【业炎】的代价「失去 3 点体力」，但机制住在引擎里（``LIFE_DECREASE_AFTER`` 按
    ``amount`` 逐点派发），故这里直接压一条 ``amount=3`` 的扣减帧：既钉住"一次扣减仍只
    落**一条**时机记录"，又钉住"三个点各触发一次【诈降】"。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_huanggai"), SeatSpec()],
        deck=deck_excluding(PACK),
    )
    engine = Engine.from_state(state, seed=7)
    before = len(engine.state.player(0).hand)

    engine.push_frame(
        FrameKind.LIFE_DECREASE,
        {"target": 0, "source": None, "amount": 3},
        timing=Timing.LIFE_DECREASE_BEFORE,
    )
    for _ in range(400):
        if not engine.stack:
            break
        engine._step()

    assert not engine.stack, "扣减帧结算完毕"
    assert engine.state.player(0).hp == 1, "失去 3 点体力（4 → 1）"
    assert len(engine.state.player(0).hand) == before + 9, "三个点各摸三张"
    records = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.LIFE_DECREASE_AFTER
    ]
    assert len(records) == 1, "一条扣减帧仍然只落一条时机记录（逐点点数只多派发轮次）"
