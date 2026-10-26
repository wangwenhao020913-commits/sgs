"""回合环与最短牌堆的平局路径（票 18 的主缝验收）。

落点 = 库门面：低层入口 ``Engine.from_state``（人为指定局面）+ 唯一终局入口。
"""

from __future__ import annotations

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.state import MoveReason, Phase, Zone
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, run_turns

PACK = load_content_pack(deck_profile="basic_53")


def _run(state, seed: int = 1, *, turns: int | None = 1):
    """跑一局；``turns`` 给出时只跑到第 ``turns`` 个回合走完。

    重洗落地后（票 23）被动局面不再因牌堆耗尽收场，故默认只跑一个回合；
    需要终局（如"两堆皆空"）的用例显式传 ``turns=None``。
    """
    engine = Engine.from_state(state, seed=seed)
    players = [RecordingPlayer(), RecordingPlayer()]
    if turns is None:
        engine.run(players)
    else:
        run_turns(engine, players, turns=turns)
    return engine


def _timings(engine, *, after: int = 0, before: int | None = None):
    values = [
        record.timing
        for record in engine.records
        if record.kind == "timing" and record.seq > after and (before is None or record.seq < before)
    ]
    return values


def _small_position(*, deck, discard=(), hand_a=(), hand_b=(), hp_a=4, hp_b=4):
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hp=hp_a, hand=hand_a), SeatSpec(hp=hp_b, hand=hand_b)],
        deck=deck,
        discard=discard,
        turn_seat=0,
        phase=Phase.PREPARE,
    )


def test_a_turn_walks_every_phase_in_order_and_skips_intervals():
    engine = _run(_small_position(deck=tuple(range(10)), hand_a=(40, 41, 42, 43)))
    first_turn = _timings(engine, before=_first_turn_after_end(engine))

    assert first_turn == [
        Timing.GAME_START,
        Timing.TURN_BEFORE_START,
        Timing.TURN_START,
        Timing.PHASE_PREPARE,
        Timing.PHASE_JUDGE,
        Timing.PHASE_DRAW_START,
        Timing.PHASE_DRAW,
        Timing.PHASE_DRAW_END,
        Timing.PHASE_PLAY_START,
        Timing.PHASE_PLAY_IDLE,
        Timing.PHASE_PLAY_END,
        Timing.PHASE_DISCARD_START,
        Timing.PHASE_DISCARD,
        Timing.PHASE_DISCARD_END,
        Timing.PHASE_FINISH,
        Timing.TURN_END,
        Timing.TURN_AFTER_END,
    ]
    assert not [timing for timing in first_turn if timing.value.startswith("INTERVAL_")]


def test_empty_phases_emit_only_their_body():
    engine = _run(_small_position(deck=tuple(range(10)), hand_a=(40, 41, 42, 43)))
    emitted = set(_timings(engine))

    for absent in (
        Timing.PHASE_PREPARE_START,
        Timing.PHASE_PREPARE_END,
        Timing.PHASE_JUDGE_START,
        Timing.PHASE_JUDGE_END,
        Timing.PHASE_FINISH_START,
        Timing.PHASE_FINISH_END,
    ):
        assert absent not in emitted


def test_discard_phase_discards_down_to_current_hp():
    engine = _run(_small_position(deck=(0, 1), hand_a=(40, 41, 42, 43)))

    discards = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DISCARD
    ]
    assert len(discards) == 1, "这一局只应有一次弃牌操作"
    assert len(discards[0].cards) == 2, "手牌 6、体力 4 ⇒ 弃 2 张"
    assert discards[0].from_zone.zone.value == "hand"
    assert discards[0].to_zone.zone.value == "discard"
    assert len(engine.state.player(0).hand) == engine.state.player(0).hp


def test_short_draw_ends_the_game_at_the_single_entry_and_game_end_is_last():
    engine = _run(_small_position(deck=(0,)), turns=None)
    tail = engine.records[-1]

    assert tail.kind == "game_end"
    assert tail.result == "draw"
    assert tail.winner is None
    assert tail.reason == "both_decks_empty"
    assert tail.check_seq == _short_draw_move_seq(engine)
    assert not [record for record in engine.records if record.kind == "frame"], (
        "平局路径没有结算帧在飞，故 DISCARD_FRAME 预期 0 条"
    )
    assert engine.finished


def test_a_nonempty_discard_pile_is_reshuffled_instead_of_ending_the_game():
    """票 23：牌堆只剩 1 张、弃牌堆非空 ⇒ 重洗补足，**不**再报 `deck_exhausted`。"""
    engine = _run(_small_position(deck=(0,), discard=(1, 2, 3)))

    reshuffles = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "deck_shuffle"
        and record.from_zone is not None
    ]
    assert len(reshuffles) == 1
    assert reshuffles[0].from_zone.zone.value == "discard"
    assert not [record for record in engine.records if record.kind == "game_end"]


def test_an_ample_draw_does_not_end_the_game():
    engine = _run(_small_position(deck=tuple(range(10)), hand_a=(40, 41, 42, 43)))
    first_draw = next(
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_move"
    )

    assert not engine.finished, "足额摸到 2 张就不该有任何终局"
    assert [card.card_id for card in first_draw.cards] == [0, 1]
    assert engine.records[-1].kind != "game_end"


def test_the_game_starts_with_the_configured_first_seat():
    state = _small_position(deck=tuple(range(10)))
    state.rule_config = state.rule_config.model_copy(update={"first_seat": 1})

    engine = _run(state)

    first_turn = next(
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_START
    )
    assert first_turn.actor == 1


def test_turn_rotation_walks_the_survivor_ring_and_skips_the_dead():
    """票 23：回合只在存活座位之间轮转（座位环真源），死者不占回合。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(alive=False), SeatSpec(), SeatSpec()],
        deck=tuple(range(40)),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1)

    run_turns(engine, [RecordingPlayer() for _ in range(4)], turns=3)

    starts = [
        record.actor
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_START
    ]
    assert starts[:4] == [0, 2, 3, 0], "座位 1 阵亡 ⇒ 轮转 0 → 2 → 3 → 0（绕过死者）"
    assert 1 not in starts


def _first_turn_after_end(engine) -> int:
    """第一个回合结束后的下一条记录序号（用作"本回合"的右开区间）。"""
    for record in engine.records:
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END:
            return record.seq + 1
    raise AssertionError("这一局没有走完一个回合")


def _short_draw_move_seq(engine) -> int:
    moves = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_move"
    ]
    return moves[-1].seq


# ---------------------------------------------------------------------------
# 翻面（本票的机制）：背面朝上 ⇒ 回合开始前翻回正面 + 整个回合被跳过
# ---------------------------------------------------------------------------


def _flip(state, seat: int, *, seed: int = 1):
    """造局后直接把座位 ``seat`` 翻到背面（只读场景入口：写入口仍是 ``set_flipped``）。"""
    engine = Engine.from_state(state, seed=seed)
    engine.set_flipped(None, seat, True)
    return engine


def _before_start_seqs(engine) -> list[int]:
    """每条「回合开始前」记录的序号（按记录序）。"""
    return [
        record.seq
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_BEFORE_START
    ]


def test_a_flipped_character_flips_back_and_skips_the_whole_turn():
    """背面朝上 ⇒ 回合开始前翻回正面，然后六阶段一个都不跑（规则集 13.2）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(40, 41)), SeatSpec(hand=(50, 51))],
        deck=tuple(range(10)),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = _flip(state, 0)
    # 跳过座位 0 之后才轮到座位 1 的正常回合；跑到它走完即可。
    run_turns(engine, [RecordingPlayer(), RecordingPlayer()], turns=2)

    starts = _before_start_seqs(engine)
    assert len(starts) >= 2, "两轮「回合开始前」都该落记录"
    skipped = _timings(engine, after=starts[0], before=starts[1])
    for absent in (
        Timing.TURN_START,
        Timing.PHASE_PREPARE,
        Timing.PHASE_JUDGE,
        Timing.PHASE_DRAW,
        Timing.PHASE_PLAY_START,
        Timing.PHASE_DISCARD_START,
        Timing.PHASE_FINISH,
        Timing.TURN_END,
        Timing.TURN_AFTER_END,
    ):
        assert absent not in skipped, f"被跳过的回合不该发 {absent}"
    flips = [
        (record.seat, record.flipped)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "flipped_change"
    ]
    assert flips == [(0, True), (0, False)], "造局翻过去一条、回合开始前翻回来一条"
    assert engine.state.player(0).flipped is False
    skipped_draws = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DRAW
        and record.to_zone.zone is Zone.HAND
        and record.to_zone.seat == 0
        and starts[0] < record.seq < starts[1]
    ]
    assert skipped_draws == [], "跳过 ⇒ 摸牌阶段也没跑（窗口内没有给座位 0 的摸牌）"
    # 下一轮（座位 1 的回合）照常：有回合开始时与准备阶段。
    second = _timings(engine, after=starts[1], before=_first_turn_after_end(engine))
    assert Timing.TURN_START in second and Timing.PHASE_PREPARE in second


def test_flipping_during_ones_own_turn_does_not_skip_that_turn():
    """在自己回合里被翻面不影响当前回合：翻面只在**自己的回合开始前**结算。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(40, 41, 42, 43)), SeatSpec()],
        deck=tuple(range(10)),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1)
    players = [RecordingPlayer(), RecordingPlayer()]
    # 先跑到出牌阶段（此刻"回合开始前"已经过去），再翻面。
    while not any(
        record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
        for record in engine.records
    ):
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    mark = engine.records[-1].seq
    engine.set_flipped(None, 0, True)
    run_turns(engine, players, turns=1)

    rest = _timings(engine, after=mark, before=_first_turn_after_end(engine))
    assert Timing.PHASE_DISCARD_START in rest and Timing.TURN_AFTER_END in rest, (
        "本回合照常走完（翻面不打断当前回合）"
    )
    assert engine.state.player(0).flipped is True, "翻面状态留给下一个自己的回合"


def test_a_back_facing_character_can_still_play_cards_in_another_turn():
    """背面朝上只影响"自己的回合开始前"：别人回合里照样能被要求出【闪】、照样能出。"""
    slash, dodge = 40, 50
    # 先手钉在座位 1（无身份局读 ``rule_config.first_seat``），这样座位 0 的"回合开始前"
    # 还没到 —— 我们要看的是"别人回合里，背面角色照样能出牌"。
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(dodge,)), SeatSpec(hand=(slash,))],
        deck=tuple(range(10)),
        turn_seat=1,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=1),
    )
    engine = Engine.from_state(state, seed=1)
    engine.set_flipped(None, 0, True)
    seen: list[tuple[str, bool, int]] = []

    class _Defender:
        """座位 0：记下"被问到时我是不是背面"，其余走最小合法应答。"""

        def respond(self, query):
            seen.append((query.kind, engine.state.player(0).flipped, query.actor))
            if query.kind == "PLAY_PHASE":
                for candidate in query.legal_actions:
                    if getattr(candidate, "kind", None) == "EndPhase":
                        return candidate
            return query.legal_actions[0]

    class _Attacker:
        """座位 1：出牌阶段用第一张能用的牌（这里是【杀】）。"""

        def respond(self, query):
            if query.kind == "PLAY_PHASE":
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "UseCard":
                        return action
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "EndPhase":
                        return action
            return query.legal_actions[0]

    run_turns(engine, [_Defender(), _Attacker()], turns=1)

    dodge_asks = [entry for entry in seen if entry[0] == "DODGE"]
    assert dodge_asks, "背面角色在别人的回合里照样进【闪】窗口"
    assert all(entry[1] for entry in dodge_asks), "被问【闪】的那一刻仍是背面朝上"
    assert all(entry[2] == 0 for entry in dodge_asks), "【闪】窗口发给背面角色本人"


def test_set_flipped_is_idempotent_and_only_emits_on_change():
    """写入口唯一且幂等：同值重复写不落 delta。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=tuple(range(10)),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=1)

    assert engine.set_flipped(None, 1, True) is True
    assert engine.set_flipped(None, 1, True) is False
    assert engine.set_flipped(None, 1, False) is True
    flips = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "flipped_change"
    ]
    assert [(record.seat, record.flipped) for record in flips] == [(1, True), (1, False)]


def _step_until(engine, predicate, players) -> None:
    """手动推进到 ``predicate(engine)`` 成立（绕开 ``run_turns`` 的回合计数口径）。"""
    for _ in range(4000):
        if predicate(engine):
            return
        if engine.pending is None:
            engine._step()
            if engine.finished:
                raise AssertionError("局终了但条件没出现")
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没到达停止条件")


def _turn_start_seats(engine) -> list[int]:
    return [
        record.actor
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_START
    ]


def test_a_skipped_turn_does_not_clear_the_jie_luoyi_bonus():
    """被翻面跳过的回合**不清理**「直到你的下个回合开始」类作用域（用户裁定的联动）。

    【界裸衣】的清理点只在**该座位自己的 `TURN_START`**；翻面跳过不发 `TURN_START`
    ⇒ 加伤保留到下一次真正开始的回合。这条是"跳过 = 没有进入回合"的可观测后果，
    也是它与其他实现（逐阶段标记）的分水岭。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_xuchu"), SeatSpec()],
        deck=tuple(range(10, 60)),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1)
    engine.set_luoyi_until_next_turn(0, True)
    engine.set_flipped(None, 0, True)
    players = [RecordingPlayer(), RecordingPlayer()]

    # ① 座位 0 的"回合开始前"到了、翻回正面、回合被跳过；随后座位 1 的正常回合开始。
    _step_until(engine, lambda e: _turn_start_seats(e) == [1], players)
    assert engine.state.player(0).flipped is False, "跳过时已翻回正面"
    assert engine.state.player(0).luoyi_until_next_turn is True, "跳过的回合不清理裸衣"

    # ② 轮到座位 0 真正开始的回合：`TURN_START` 到了，这一份加伤才清掉。
    _step_until(engine, lambda e: _turn_start_seats(e)[-1] == 0, players)
    assert engine.state.player(0).luoyi_until_next_turn is False, "真正开始的回合才清"
