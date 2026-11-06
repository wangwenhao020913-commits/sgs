"""回合环与最短牌堆的平局路径（票 18 的主缝验收）。

落点 = 库门面：低层入口 ``Engine.from_state``（人为指定局面）+ 唯一终局入口。
"""

from __future__ import annotations

from pathlib import Path

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, record_json
from sgs.state import (
    ActivateSkill,
    FrameKind,
    MoveReason,
    Phase,
    QueryKind,
    SelectOption,
    SelectOptionContext,
    Zone,
)
from sgs.timing import Timing

from support import (
    RecordingPlayer,
    SeatSpec,
    build_state,
    find_action,
    run_queries,
    run_turns,
)

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


# ---------------------------------------------------------------------------
# L0「游戏开始时」的**派发点**（票 03）：帧外时机的又一个消费者
#
# 今天 ``GAME_START`` 只有枚举 + 记录（``_start`` 里 ``emit_timing``），挂在它上面的技能
# 永不触发；``_start`` 在钩子发问挂起时还会把 ``_started`` 置真、复入直接进回合环、
# 截断效果。这一组用例用 ``CATALOG`` 注入的合成钩子钉派发点本身（票 03 的 DoD）——
# 不引入任何真实武将，故既有对局的记录流一行不动。
# ---------------------------------------------------------------------------

GAME_START_SKILL = "hero.fake.game_start"
GAME_START_SKILL_B = "hero.fake.game_start_b"
"""合成 L0 技能的 id（只活在 ``CATALOG`` 注入里）；两个座位各一条 ⇒ 必须是两个 id。

``ask_skill_activation`` 的帧内答案缓存键 = ``(timing, 派发键, 嵌套层号, 座位, 轮次)``
（票 01 起把座位 / 轮次 / 层号并进键里；改造前是 ``(skill_id, key)``、不含座位）。故
"同一条 id 授给两个座位"不再是缓存问题：两问各有各的槽。本用例用两条不同 id 只是按座位
各声明一条钩子（``observed`` 记的是 ``skill_owner``），判据落在**派发次序**上。
"""

_GAME_START_OPTION = "draw-one"

_GAME_START_EFFECT_STARTED = "_fake_game_start_effect_started"
"""帧私有守卫：本钩子的效果已发起。派发环在**别的实例**挂起后重入时会从头再跑一遍已完成的
钩子（``_dispatch`` 的既有重入口径），没有它就会再摸一张。"""


def _install_game_start_skill(monkeypatch, skill_id, handler, *, locked=False):
    """把一条**只挂在 ``GAME_START`` 上**的合成技能注入 ``CATALOG``（合成钩子的唯一入口）。"""
    from types import MappingProxyType

    from sgs.effects import CarrierKind
    from sgs.skills import Hook, SkillDef
    from sgs.skills import catalog as catalog_module

    definition = SkillDef(
        skill_id=skill_id,
        locked=locked,
        carrier=CarrierKind.HERO,
        hooks=(Hook(Timing.GAME_START, handler),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, skill_id: definition}),
    )


def _asking_game_start_handler(observed):
    """合成 L0 钩子：先问一次选项（可挂起），答完摸 1 张；顺带记下 L0 帧的两条事实。"""

    def handler(host, frame):
        observed.append(
            (
                frame.data["skill_owner"],
                frame.data.get("source"),
                frame.data.get("hook_owner"),
            )
        )
        seat = frame.data["skill_owner"]
        answer = host.ask_query(
            QueryKind.SELECT_OPTION,
            seat,
            Timing.GAME_START,
            frame=frame,
            payload=SelectOptionContext(
                skill_id=GAME_START_SKILL, options=(_GAME_START_OPTION,)
            ),
        )
        if host.suspended(answer):
            return "stay"
        if frame.data.get(_GAME_START_EFFECT_STARTED):
            return None
        frame.data[_GAME_START_EFFECT_STARTED] = True
        control = host.draw_for_hand(frame, seat, 1)
        return "stay" if control == "stay" else None

    return handler


def _quiet_game_start_handler(observed):
    """合成 L0 钩子：只记下"谁被派发到了 + L0 帧的两条事实"，不问、不压帧。"""

    def handler(host, frame):
        observed.append(
            (
                frame.data["skill_owner"],
                frame.data.get("source"),
                frame.data.get("hook_owner"),
            )
        )
        return None

    return handler


class _GameStartPlayer:
    """应答合成 L0 钩子：发动（``ActivateSkill``）+ 取唯一选项；其余走最小合法应答。"""

    def __init__(self) -> None:
        self._fallback = RecordingPlayer()
        self.seen: list = []

    def respond(self, query):
        self.seen.append(query)
        activate = find_action(query, lambda action: isinstance(action, ActivateSkill))
        if activate is not None:
            return activate
        option = find_action(query, lambda action: isinstance(action, SelectOption))
        if option is not None:
            return option
        return self._fallback.respond(query)


def _first_timing(engine, timing: Timing):
    return next(
        (
            record
            for record in engine.records
            if record.kind == "timing" and record.timing is timing
        ),
        None,
    )


def _game_start_engine(*, seats: int = 2):
    """真开局（含起始手牌）之后、第一次 ``_step`` 之前：把合成 L0 技能授予座位 0。"""
    engine = Engine.new_game(
        seed=7, rule_config=RuleConfig(player_count=seats), pack=PACK
    )
    engine.grant_skill(0, GAME_START_SKILL)
    return engine


def test_a_game_start_hook_is_asked_after_the_deal_and_before_the_first_turn(monkeypatch):
    """L0.3 的位置 = 起始手牌**发完后**、第一个回合**之前**（规则 §3.9 L229–L233）。

    判据比较**记录序本身**（`CODING_STANDARDS.md` §4：不 ``sorted()`` / 不去重）：
    发牌时机 < ``GAME_START`` 记录 < 钩子的两个询问 < 第一条 ``TURN_BEFORE_START``。
    效果（摸 1 张）落在同一个窗口里且**只落一次**。
    """
    _install_game_start_skill(
        monkeypatch, GAME_START_SKILL, _asking_game_start_handler([])
    )
    engine = _game_start_engine()
    players = [_GameStartPlayer(), _GameStartPlayer()]
    run_queries(
        engine,
        players,
        lambda e: _first_timing(e, Timing.TURN_BEFORE_START) is not None,
    )

    deal = _first_timing(engine, Timing.GAME_INITIAL_HAND_DEALING)
    start = _first_timing(engine, Timing.GAME_START)
    turn = _first_timing(engine, Timing.TURN_BEFORE_START)
    asks = [
        record
        for record in engine.records
        if record.kind == "query" and record.query.skill_id == GAME_START_SKILL
    ]

    assert deal is not None and start is not None and turn is not None
    assert [record.query.kind for record in asks] == [
        "SKILL_ACTIVATION",
        "SELECT_OPTION",
    ], "钩子先被问『是否发动』、再问自己的选项（两问各一次）"
    assert deal.seq < start.seq < asks[0].seq < asks[-1].seq < turn.seq, (
        "『游戏开始时』在起始手牌发完之后、第一回合之前；实得 "
        f"发牌 {deal.seq} / GAME_START {start.seq} / 询问 "
        f"{[record.seq for record in asks]} / 回合 {turn.seq}"
    )

    draws = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone.zone is Zone.HAND
        and record.to_zone.seat == 0
        and record.seq > start.seq
    ]
    assert len(draws) == 1, "钩子的效果（摸 1 张）只落一次"
    assert len(engine.state.player(0).hand) == 5, "起始手牌 4 + 钩子摸的 1"


def test_a_suspended_game_start_hook_resumes_without_re_asking_and_finishes_its_effect(
    monkeypatch,
):
    """``_start`` 可停可续：挂起时 ``_started`` 留假，复入原地续跑、不重问、效果完整。

    这也是 A2 的分水岭：L0 帧 ``source`` 恒 ``None``（不把"游戏开始时"伪装成第一个座位的
    回合事件），"关于我"自校走 ``hook_owner``（``MATCH_OWNER_KEY``）。
    """
    observed: list[tuple[object, object, object]] = []
    _install_game_start_skill(
        monkeypatch, GAME_START_SKILL, _asking_game_start_handler(observed)
    )
    engine = _game_start_engine()
    players = [_GameStartPlayer(), _GameStartPlayer()]

    # ① 第一次推进 = L0 派发：'是否发动'问出去就挂起。
    engine._step()
    first = engine.pending
    assert first is not None
    assert first.kind == "SKILL_ACTIVATION" and first.skill_id == GAME_START_SKILL
    assert engine._started is False, (
        "挂起时 ``_started`` 必须留假：置真会让复入直接进回合环、把钩子效果截断"
    )
    engine.submit(players[first.actor].respond(first))

    # ② 复入：不再重问"是否发动"，问钩子自己的选项。
    engine._step()
    second = engine.pending
    assert second is not None
    assert second.kind == "SELECT_OPTION" and second.actor == 0
    assert engine._started is False
    engine.submit(players[second.actor].respond(second))

    # ③ 续跑：答完选项，效果真的落地，派发才算完。
    engine._step()
    assert engine.pending is None
    assert engine._started is True

    questions = [
        (record.query.kind, record.query.actor)
        for record in engine.records
        if record.kind == "query" and record.query.skill_id == GAME_START_SKILL
    ]
    assert questions == [("SKILL_ACTIVATION", 0), ("SELECT_OPTION", 0)], (
        f"挂起 → 答 → 续跑整条跑完，同一个问只问一次；实得 {questions}"
    )
    assert len(
        [
            record
            for record in engine.records
            if record.kind == "timing" and record.timing is Timing.GAME_START
        ]
    ) == 1, "时机记录也去重：挂起 → 续跑只落一条 GAME_START（_turn_data 的键作幂等闸）"
    draws = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone.zone is Zone.HAND
        and record.to_zone.seat == 0
        and record.seq > _first_timing(engine, Timing.GAME_START).seq
    ]
    assert len(draws) == 1, "效果完整且不重复（摸 1 张）"
    assert len(engine.state.player(0).hand) == 5
    assert observed, "钩子真的跑过"
    assert set(observed) == {(0, None, 0)}, (
        "L0 帧的记录/帧都不挂在第一个座位的回合上（source=None、记录 actor=None），"
        f"『关于我』自校走 hook_owner；实得 {observed}"
    )


def test_a_constructed_engine_with_a_pre_pushed_frame_still_starts_the_game_first():
    """构造局面（``from_state`` + ``push_frame``）仍**先**派发 ``GAME_START``（A2 的不变式）。

    "``_started`` 假 ∧ 结算栈非空"有两种来源，次序必须分开（见 :meth:`Engine._step` 的注释）：

    - L0 钩子压过子帧 / 挂起在询问上 ⇒ **先让栈出空**，再重入 ``_start``（上一个用例）；
    - **构造局面**：``Engine.from_state(...)`` 之后、第一次 ``_step`` 之前就 ``push_frame(...)``
      （``tests/test_hero_damage_after.py`` 这类时点单测的标准造法）⇒ L0 一次都没派发过，
      仍走原次序先 ``_start``。否则 ``GAME_START`` 被推迟到预压帧清空之后、甚至永不发出。
    """
    state = _small_position(deck=tuple(range(10)), hp_a=3)
    engine = Engine.from_state(state, seed=1)
    frame = engine.push_frame(
        FrameKind.RECOVER,
        {
            "source": 1,
            "targets": (0,),
            "card": None,
            "use_timing": None,
            "amount": 1,
        },
        timing=Timing.RECOVER_VALUE,
    )

    # 第一次 _step：只派发 L0（GAME_START），预压帧原地不动。
    engine._step()
    start = _first_timing(engine, Timing.GAME_START)
    assert start is not None, "构造局面 + 预压帧：第一次 _step 仍要发 GAME_START"
    assert engine._started is True, "派发完成才置真（后续步骤据此进回合环 / 出栈）"
    assert engine.stack.top is frame, (
        "预压帧要等 L0 派发完才轮得到：栈非空不能把 _start 挡在后面"
    )
    assert _first_timing(engine, Timing.RECOVER_VALUE) is None, (
        "L0 那一步不跑预压帧（先 _start、再栈，与票 01 之前同一粒度）"
    )

    # 之后照常按结算栈推进：帧自己的时机记录全部落在 GAME_START 之后。
    engine._step()
    value = _first_timing(engine, Timing.RECOVER_VALUE)
    assert value is not None and value.seq > start.seq, (
        "预压帧的时机记录必须落在 GAME_START 之后；实得 "
        f"GAME_START {start.seq} / RECOVER_VALUE {value.seq if value is not None else None}"
    )


def test_two_seats_with_game_start_hooks_are_dispatched_in_seat_order(monkeypatch):
    """多座位同时有 L0 技能 ⇒ 按座位升序派发（本构造 = 无身份局，先手座位 0，环序即升序）。

    同座位内部仍按链序（``priority_class`` → ``skill_id``）；本用例两条都是武将档、
    座位不同，故次序只由座位决定。钩子是锁定技（不问），观测面就是派发顺序本身。
    """
    observed: list[tuple[object, object, object]] = []
    _install_game_start_skill(
        monkeypatch,
        GAME_START_SKILL,
        _quiet_game_start_handler(observed),
        locked=True,
    )
    _install_game_start_skill(
        monkeypatch,
        GAME_START_SKILL_B,
        _quiet_game_start_handler(observed),
        locked=True,
    )
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], deck=tuple(range(10, 60)))
    state.player(0).granted_skills.append(GAME_START_SKILL)
    state.player(1).granted_skills.append(GAME_START_SKILL_B)
    engine = Engine.from_state(state, seed=1)

    engine._step()  # 第一次推进 = L0 派发（回合环一步都还没走）

    assert observed == [(0, None, 0), (1, None, 1)], (
        "两个座位各挂一条 ⇒ 座位 0 先于座位 1；两帧都不伪装成第一个座位的回合事件"
        f"（source=None、hook_owner=持有者）；实得 {observed}"
    )
    assert engine._started is True, "派发完才置真"
    assert not [record for record in engine.records if record.kind == "frame"], (
        "L0 帧不压栈：派发只用那条不压栈的持久帧（frame_id = 0），记录流里没有 push/pop"
    )


def test_the_no_hook_game_start_record_is_byte_identical_to_the_frozen_replay():
    """无钩子时只落**与今天同一条**记录：字节级对照冻结基准（票 03 DoD 第 2 条）。

    基准 01 的开局参数与本用例相同（seed 101 / 2 座 / 起始手牌 4 / basic_53），故它的
    ``GAME_START`` 行就是"今天"的规范字节；同时断"没有钩子 ⇒ 一条帧都不建"。
    """
    frozen = next(
        line
        for line in (
            Path(__file__).resolve().parent / "replays" / "01-slash-dodge-cancel.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if '"timing":"GAME_START"' in line
    )
    engine = Engine.new_game(seed=101, rule_config=RuleConfig(player_count=2), pack=PACK)
    run_turns(engine, [RecordingPlayer(), RecordingPlayer()], turns=1)

    record = _first_timing(engine, Timing.GAME_START)
    assert record is not None
    assert record.actor is None and record.source is None, "不伪装成第一个座位的回合事件"
    assert record_json(record) == frozen, "无钩子 ⇒ 这条记录逐字节不变"
    first_turn = _first_timing(engine, Timing.TURN_BEFORE_START)
    assert first_turn is not None
    assert not [
        other
        for other in engine.records
        if other.kind == "frame" and other.seq < first_turn.seq
    ], "没有 L0 钩子 ⇒ 一个帧都不建"


# ---------------------------------------------------------------------------
# 「结束出牌阶段」的受管写入口（lei-shenganning 票 92）
#
# 用户 2026-11-07 裁定："就像是玩家点击结束回合按钮那样，直接结束出牌阶段。并非跳过，而是结束"。
# 落点 = ``Engine.end_play_phase``（`_phase_play_idle` 消费）——与玩家回 ``EndPhase`` 同一条
# 收尾路径；与 ``skip_play_phase``（只在 ``_phase_play_start`` 消费）不是一个时点。
#
# 写入口只该在**结算**里被调用（技能效果 / 钩子），不是在被问的玩家那里——故下面用
# "座位 1 在【闪】窗口里替座位 0 结束出牌阶段"来模拟：那正是"出牌阶段里的一次子结算"。
# ---------------------------------------------------------------------------


def _play_phase_action(query):
    """出牌阶段里一条**非结束**的合法动作（用一张牌）。"""
    for action in query.legal_actions:
        if getattr(action, "kind", None) != "EndPhase":
            return action
    return next(
        action for action in query.legal_actions if getattr(action, "kind", None) == "EndPhase"
    )


class _PlaysCards:
    """一直出第一张能用的牌；从不结束阶段（结束只可能来自 ``end_play_phase``）。"""

    def __init__(self) -> None:
        self.play_queries = 0
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            self.play_queries += 1
            return _play_phase_action(query)
        return self._fallback.respond(query)


class _EndsSomebodysPlayPhase:
    """在**自己的第一次响应窗口**里替 ``seat`` 结束出牌阶段（模拟一次子结算里的技能效果）。

    座位 1 的【闪】窗口落在座位 0 用【杀】的结算里 ⇒ 正是"出牌阶段之内、子帧之中"。
    ``seat=None`` = 对照组（什么都不调）。
    """

    def __init__(self, engine, *, seat: int | None) -> None:
        self.engine = engine
        self.seat = seat
        self.calls = 0
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if query.kind == QueryKind.DODGE and self.calls == 0 and self.seat is not None:
            self.calls += 1
            self.engine.end_play_phase(self.seat)
        return self._fallback.respond(query)


def _slash_position():
    """座位 0 手里一张【杀】（id 10 = basic_53 的【杀】），牌堆是 0..9。"""
    return _small_position(deck=tuple(range(10)), hand_a=(10,))


def _used_cards(engine) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.USE
    ]


def test_end_play_phase_finishes_the_idle_loop_like_the_button():
    engine = Engine.from_state(_slash_position(), seed=1)
    actor = _PlaysCards()
    other = _EndsSomebodysPlayPhase(engine, seat=0)
    run_turns(engine, [actor, other], turns=1)

    assert other.calls == 1, "【闪】窗口确实出现过（那张【杀】真的结算了）"
    assert _used_cards(engine), "那张【杀】照常用了（效果是在这一段结算完之后才收尾）"
    assert actor.play_queries == 1, (
        "子结算里结束了出牌阶段 ⇒ 不该再来第二问；"
        f"实得 {actor.play_queries} 问"
    )
    first_turn = _timings(engine, before=_first_turn_after_end(engine))
    assert Timing.PHASE_PLAY_END in first_turn and Timing.PHASE_DISCARD_START in first_turn, (
        "结束之后照常走 出牌阶段结束时 → 弃牌阶段"
    )
    assert first_turn.count(Timing.PHASE_PLAY_IDLE) == 1, "本回合只进一次出牌阶段空闲步"


def test_without_end_play_phase_the_idle_loop_asks_again():
    """对照：同样出那张【杀】，但不调写入口 ⇒ 出牌阶段会**再问一次**（标志才是原因）。"""
    engine = Engine.from_state(_slash_position(), seed=1)
    actor = _PlaysCards()
    other = _EndsSomebodysPlayPhase(engine, seat=None)
    run_turns(engine, [actor, other], turns=1)

    assert actor.play_queries >= 2, (
        "没有受管写入口时，出牌阶段用完牌后照常再问一次；"
        f"实得 {actor.play_queries} 问"
    )


def test_end_play_phase_is_a_no_op_before_the_play_phase():
    """与 ``skip_play_phase`` 的区别：阶段**还没开始**时调用 ⇒ 什么都不发生。"""
    engine = Engine.from_state(_slash_position(), seed=1)
    engine.end_play_phase(0)  # 局面还在准备阶段（``_small_position`` 的 phase=PREPARE）
    actor = _PlaysCards()
    run_turns(engine, [actor, RecordingPlayer()], turns=1)

    assert actor.play_queries >= 2, "出牌阶段照常进行（不是跳过，也不是结束）"


def test_end_play_phase_of_another_seat_is_a_no_op():
    """别人的回合里调用 ⇒ 静默 no-op（不是编程错误，只是没有效果）。"""
    engine = Engine.from_state(_slash_position(), seed=1)
    actor = _PlaysCards()
    other = _EndsSomebodysPlayPhase(engine, seat=1)
    run_turns(engine, [actor, other], turns=1)

    assert other.calls == 1, "【闪】窗口确实出现过"
    assert actor.play_queries >= 2, (
        "调的是座位 1，当前回合角色是座位 0 ⇒ 座位 0 的出牌阶段不受影响；"
        f"实得 {actor.play_queries} 问"
    )
