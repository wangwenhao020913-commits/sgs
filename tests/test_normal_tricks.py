"""票 27：多目标结算与普通锦囊（库门面主缝）。

断言全部落在**记录流与棋盘状态**上：时机 / delta / query / action、体力、区域、手牌数。
不碰帧私有 ``data`` / ``pc``，也不碰注册表内部结构。

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

import pytest

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.state import (
    EndPhase,
    Pass,
    PlayCard,
    QueryKind,
    SelectCards,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support.positions import SeatSpec, build_state, cards_of_type, run_until

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
    """RESPOND 窗口的应答：``play`` = 打出第一张候选、``use`` = 使用第一张候选、
    ``pass`` = 放弃。"""

    pick: int = 0
    use_target: int | None = None
    played: bool = field(default=False)


class _Planned:
    def __init__(self, plan: _Plan) -> None:
        self.plan = plan

    def respond(self, query):
        plan = self.plan
        if query.kind == QueryKind.PLAY_PHASE:
            if not plan.played:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == plan.play:
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
) -> tuple[Engine, _Plan]:
    """构造局面 → 跑到"这张牌的使用结算结束后"即止。"""
    plan = _Plan(play=play, respond=respond, pick=pick, use_target=use_target)
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=deck if deck is not None else _deck_excluding(pack, *used),
        turn_seat=turn_seat,
        rule_config=RuleConfig(first_seat=turn_seat),
    )
    engine = Engine.from_state(state, seed=seed)
    players = [_Planned(plan) for _ in seats]

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


def test_the_six_query_kinds_and_the_window_causes_are_defined():
    assert {kind.value for kind in QueryKind} == {
        "PLAY_PHASE",
        "DODGE",
        "PEACH",
        "DISCARD",
        "RESPOND",
        "SELECT_CARDS",
    }
    assert {cause.value for cause in WindowCause} == {
        "NULLIFY",
        "BARBARIAN_INVASION",
        "ARROW_BARRAGE",
        "DUEL",
        "BORROWED_SWORD",
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
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    engine, _ = _run_trick(
        pack,
        play="borrowed_sword",
        seats=[SeatSpec(hand=(borrowed,)), SeatSpec(equip=(weapon,)), SeatSpec()],
        deck=_deck_excluding(pack, borrowed, weapon),
        turn_seat=0,
        respond="pass",
    )

    assert weapon in engine.state.player(0).hand
    assert engine.state.player(1).equip == []


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
