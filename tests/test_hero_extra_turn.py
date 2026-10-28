"""M6 票 11：**额外回合**与**轮次** + 界吕蒙【博图】（spec D13 / §回合与轮次）。

规则原文：`三国杀详细规则.txt` §三 L146–L260（回合流程与轮次：3.8 回合轮换规则把
「轮」定成"从当前回合角色开始，经过其余角色各一个回合、再次回到当前回合角色之前"；
额外回合插在当前回合之后、正常下家之前）与 §十九 L2133–L2145（界吕蒙）：

    博图：每轮限 X 次（X 为存活角色数且至多为 3），回合结束时，若本回合置入弃牌堆的
        牌中包含四种花色，你可以执行一个额外的回合。

本票落四件事：

1. **轮次**：引擎新增"轮"这个状态——自然指针（下一个自然回合的座位）**只在非额外回合
   结束时前进**，回到本轮起始座位时轮次 +1。
2. **额外回合 = 显式待执行栈**：回合结束时压入、下个回合开始时弹出并接管，
   **不推进自然指针**（故额外回合不会触发"回到起始座位"，也就不会无限刷新"每轮限 X 次"）。
3. **界吕蒙【博图】**：回合结束时，若本回合置入弃牌堆的牌含四种花色（**计入本回合所有
   来源置入的牌**，见 `06`（§5「程序化约定与偏离一手」） 的裁定），可以执行一个额外的回合；每轮限 X 次，
   X = 存活角色数且至多 3，**每次发动时重算**。
4. **记录流**：回合边界以 ``turn_change`` delta 落盘（额外回合 / 轮次变化都可辨认）；
   标准 47 条金录像里没有这两类载体，故逐字节不变。

外部缝：``Engine`` 的公开入口（``from_state`` / ``run`` / ``submit``）、``engine.records``
的 delta 形状、``GameState`` 的轮次字段、``effects.move_card``（弃牌花色的事实来源）。
"""

from __future__ import annotations

import pytest

from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.state import MoveReason, UseCard, Zone, ZoneRef
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")

JIE_LVMENG = "jie_lvmeng"
JIE_BOTU = "hero.jie_lvmeng.botu"

NO_HERO_SEATS = (SeatSpec(), SeatSpec())


def _turn_starts(engine) -> list[int | None]:
    """记录流里每一次 ``TURN_START`` 的座位（按序）。"""
    return [
        record.actor
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_START
    ]


def _turn_changes(engine) -> list[tuple[int, bool, int]]:
    """记录流里每一条 ``turn_change``：``(轮次, 是否额外回合, 座位)`` 按序。"""
    return [
        (record.round, record.extra, record.seat)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "turn_change"
    ]


def _two_seat_engine(*, hero: str | None = None) -> Engine:
    """两人局：座位 0（可选武将）、座位 1 无武将；座位 0 先手，牌堆足够。"""
    seats = [
        SeatSpec(hero=hero),
        SeatSpec(),
    ]
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    return Engine.from_state(state, seed=7)


def _run_natural_turns(engine: Engine, turns: int) -> Engine:
    """跑到 ``turns`` 个完整回结束（按 ``TURN_AFTER_END`` 计数）为止。"""
    return run_until(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: sum(
            1
            for record in e.records
            if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
        )
        >= turns,
    )


# ---------------------------------------------------------------------------
# ① 轮次：自然指针只在非额外回合结束时前进，回到起始座位时 +1
# ---------------------------------------------------------------------------


def test_a_round_change_is_recorded_when_the_pointer_returns_to_the_round_start_seat():
    """两人局：0 → 1 之后回到座位 0 = 第 2 轮；轮次变化以 ``turn_change`` 落盘。

    载体 = 界吕蒙（「每轮限 X 次」是轮次的唯一规则读者）：有载体时轮次变化进记录流。
    """
    engine = _two_seat_engine(hero=JIE_LVMENG)
    _run_natural_turns(engine, 2)

    assert _turn_starts(engine)[:3] == [0, 1, 0]
    assert _turn_changes(engine) == [(2, False, 0)], "第 2 轮从座位 0 起算，第一条轮次变化"


def test_a_plain_natural_turn_inside_the_round_records_no_turn_change():
    """反例（同一局面、只跑一个回合）：座位 1 的回合是**轮内**的自然回合，不是轮次变化。"""
    engine = _two_seat_engine(hero=JIE_LVMENG)
    _run_natural_turns(engine, 1)

    assert _turn_starts(engine) == [0, 1]
    assert _turn_changes(engine) == []


def test_no_turn_change_is_recorded_when_no_skill_reads_the_round():
    """没有「每轮限 X 次」载体的对局**不落**轮次 delta（47 条标准金录像逐字节不变的前提）。

    轮次在没有读者时不改变任何规则结果，故记录流保持与 M5 逐字相同；这条判据是
    "标准 108 基准不受 M6 票 11 影响"的机械证明（frozen bytes 那条在 test_replays）。
    """
    engine = _two_seat_engine()
    _run_natural_turns(engine, 4)

    assert _turn_starts(engine)[:5] == [0, 1, 0, 1, 0], "自然指针照常轮转"
    assert _turn_changes(engine) == []


# ---------------------------------------------------------------------------
# ② 额外回合：回合结束时压栈，下个回合接管，自然指针不前进
# ---------------------------------------------------------------------------

SUITS = ("spade", "heart", "club", "diamond")


def _suited_hand_for(pack) -> tuple[int, ...]:
    """四种花色各取一张（【博图】的判据是**花色种类**，不是张数）。"""
    picked: list[int] = []
    for suit in SUITS:
        picked.append(
            next(
                index
                for index, card in enumerate(pack.deck.cards)
                if card.suit.value == suit and index not in picked
            )
        )
    return tuple(picked)


def _suited_hand() -> tuple[int, ...]:
    """标准 108 档的四花色各一张。"""
    return _suited_hand_for(PACK)


class _BotuDriver:
    """座位 0 的脚本：弃牌阶段**尽量丢出四种花色**，见到【博图】就发动，其余最小应答。

    弃牌在真实路径上走（弃牌阶段 → ``move_card`` → 弃牌花色记账），故【博图】的判据是
    "本回合真的置入了这些牌"，而不是测试直接往状态里塞花色。

    **出牌阶段的补花色**（``top_up``）模拟"本回合的**其他来源**把牌置入弃牌堆"（`06`（§5「程序化约定与偏离一手」）
    的裁定：计入所有来源）：牌从牌堆经**同一个搬牌原语**进弃牌堆，故花色记账走的是生产
    路径。手牌在弃牌阶段会被压到体力值（1 张），靠摸牌补不满四种花色，额外回合尤其如此。
    """

    def __init__(
        self, engine=None, *, top_up: bool = True, keep: tuple[int, ...] = (),
        play_wine: bool = False,
    ) -> None:
        self.engine = engine
        self.top_up = top_up
        self.keep = keep
        self.play_wine = play_wine
        self.activations = 0
        self.kill_after_first: tuple[int, ...] = ()

    def _top_up_suits(self) -> None:
        """把本回合还缺的花色各从牌堆顶拿一张进弃牌堆（模拟其他来源的置入）。"""
        engine = self.engine
        state = engine.state
        missing = [suit for suit in SUITS if suit not in state.discard_suits_this_turn]
        if not missing:
            return
        picked: list[int] = []
        for suit in missing:
            for card_id in state.deck:
                if state.card(card_id).suit == suit and card_id not in picked:
                    picked.append(card_id)
                    break
        if not picked:
            return
        from sgs.effects import EffectContext, move_card
        from sgs.state import MoveReason, Zone, ZoneRef

        move_card(
            state,
            EffectContext(actor=0),
            state.refs(tuple(picked)),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.LOSE,
            owner=None,
        )

    def respond(self, query):
        from sgs.state import DiscardCards, EndPhase

        if query.kind == "PLAY_PHASE":
            if self.activations and self.kill_after_first:
                # 构造局面的捷径：把若干座位标成阵亡（死亡流程本身不是本条的被测面），
                # 用来让"存活角色数"在回合之间真的变小 ⇒ X 必须**重算**。
                for seat in self.kill_after_first:
                    self.engine.state.player(seat).alive = False
                self.kill_after_first = ()
            if self.play_wine:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == "wine":
                        return action
            if self.top_up:
                self._top_up_suits()
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == "DISCARD":
            candidates = [
                action
                for action in query.legal_actions
                if isinstance(action, DiscardCards)
            ]
            if self.keep:
                # 尽量留住指定牌（【酒】的清理测试要留一张到额外回合再用）。
                kept = [
                    action
                    for action in candidates
                    if not any(card.card_id in self.keep for card in action.cards)
                ]
                candidates = kept or candidates
            return max(
                candidates,
                key=lambda action: (
                    len(tuple(dict.fromkeys(card.suit for card in action.cards))),
                    -action.cards[0].card_id,
                ),
            )
        if query.kind == "SKILL_ACTIVATION":
            for action in query.legal_actions:
                if action.kind == "ActivateSkill" and action.skill_id == JIE_BOTU:
                    self.activations += 1
                    return action
        return PassPlayer().respond(query)


def _botu_state():
    """座位 0 = 界吕蒙、体力 1、手里四花色（弃牌阶段会把四花色丢进弃牌堆）。"""
    hand = _suited_hand() + (deck_excluding(PACK, *_suited_hand())[0],)
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hp=1, hp_max=4, hand=hand, hero=JIE_LVMENG), SeatSpec()],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )


def _botu_engine(*, top_up: bool = True) -> tuple[Engine, _BotuDriver]:
    """``_botu_state()`` + 驱动座位 0 的脚本。

    ``top_up=False``：只有弃牌阶段那一次四花色 ⇒ 额外回合里不再满足条件（只发动一次）。
    """
    driver = _BotuDriver(top_up=top_up)
    engine = Engine.from_state(_botu_state(), seed=7)
    driver.engine = engine
    return engine, driver


def test_an_extra_turn_is_inserted_right_after_the_turn_and_the_natural_pointer_stays():
    """座位 0 的自然回合 → 额外回合（座位 0 再来一次）→ 然后才是座位 1。

    "插在当前回合结算完毕之后、正常的下家回合之前"（§三 3.8）在记录流里就是
    ``[0, 0, 1]`` 的 ``TURN_START`` 序列 + 一条 ``extra=True`` 的 ``turn_change``。
    """
    engine, driver = _botu_engine(top_up=False)
    run_until(engine, [driver, PassPlayer()], lambda e: len(_turn_starts(e)) >= 3)

    assert _turn_starts(engine)[:3] == [0, 0, 1], "额外回合紧接当前回合，然后才是自然下家"
    assert driver.activations == 1
    assert _turn_changes(engine) == [(1, True, 0)], "额外回合不改变轮次，只落边界事实"


def test_an_extra_turn_does_not_refresh_the_round_limit():
    """**核心反例**：0 号位拿额外回合**不会**刷新「每轮限 X 次」（票面验收 1）。

    两人局 ⇒ X = min(2, 3) = 2：自然回合发动一次（计数 1），额外回合结束再发动一次
    （计数 2）；第二个额外回合结束时就到了 X，**不再询问**。若额外回合会刷新计数，
    第三次询问仍会出现（0 < 2），这条断言是它变红的唯一原因。
    """
    engine, driver = _botu_engine()
    run_until(engine, [driver, PassPlayer()], lambda e: len(_turn_starts(e)) >= 4)

    # 序列：0（自然）→ 0（额外 1）→ 0（额外 2）→ 1（自然下家）；X = 2 用满。
    assert _turn_starts(engine)[:4] == [0, 0, 0, 1], _turn_starts(engine)[:6]
    assert driver.activations == 2
    assert [extra for _round, extra, _seat in _turn_changes(engine)] == [True, True]


def test_the_round_limit_refreshes_when_the_round_actually_advances():
    """正例：轮次真的 +1（回到起始座位）时次数复位，故新一轮里【博图】又能发动。

    与上一条配对：**额外回合不刷新、轮次刷新**——两件事必须分得开，否则"每轮限 X 次"
    与"每回合限 X 次"没有区别。
    """
    engine, driver = _botu_engine()
    run_until(engine, [driver, PassPlayer()], lambda e: len(_turn_starts(e)) >= 6)

    # 第 1 轮：座位 0 的自然回合 + 两个额外回合（X = 2 用满）→ 座位 1 → 第 2 轮座位 0。
    assert _turn_starts(engine)[:5] == [0, 0, 0, 1, 0]
    assert _turn_changes(engine)[:3] == [(1, True, 0), (1, True, 0), (2, False, 0)], "第 2 轮从座位 0 起算"
    assert driver.activations >= 3, "新一轮的次数已复位，故还能再次发动"
    assert _turn_changes(engine)[3] == (2, True, 0), "第 2 轮的额外回合带着新的轮次"


# ---------------------------------------------------------------------------
# ③ 每轮限 X 次：X 在每次发动时按存活角色数重算
# ---------------------------------------------------------------------------


def _four_seat_engine():
    """4 人局：座位 0 = 界吕蒙、体力 1、手里四花色；其余座位无武将。"""
    hand = _suited_hand() + (deck_excluding(PACK, *_suited_hand())[0],)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=1, hp_max=4, hand=hand, hero=JIE_LVMENG, identity=Identity.LORD),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    driver = _BotuDriver()
    engine = Engine.from_state(state, seed=7)
    driver.engine = engine
    return engine, driver


def test_the_round_limit_is_recomputed_from_the_alive_count_on_every_activation():
    """X = 存活角色数且至多 3，**每次发动时重算**（`06`（§5「程序化约定与偏离一手」） 的裁定；票面验收 1 的另一半）。

    4 人局开局 X = min(4, 3) = 3。第一次发动后，脚本把座位 2 / 3 标成阵亡（构造局面的
    捷径：死亡流程本身不是本条的被测面），存活降到 2 ⇒ **X 变成 2** ⇒ 只再发动一次就
    到顶（共 2 次）。若 X 是"开场快照"（3），第三次仍会出现。
    """
    engine, driver = _four_seat_engine()
    driver.kill_after_first = (2, 3)
    run_until(engine, [driver, PassPlayer(), PassPlayer(), PassPlayer()],
              lambda e: len(_turn_starts(e)) >= 4)

    assert driver.activations == 2, "存活 2 人 ⇒ X = 2；X 若是开场快照（3）这里会是 3"
    assert _turn_starts(engine)[:4] == [0, 0, 0, 1], "两次额外回合之后轮到自然下家"


# ---------------------------------------------------------------------------
# ④ 回合末清理在额外回合边界上仍然正确（【酒】的计数与加伤）
# ---------------------------------------------------------------------------

PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 张档：标准 108 没有【酒】，而【酒】的加伤状态 / 每回合一次计数正是要被
额外回合边界检验的那类"本回合"状态。"""

WINE_COUNT_KEY = "wine"


def _wine_engine():
    """4 人局：座位 0 = 界吕蒙、体力 1、手里两种花色 + 两张【酒】。"""
    wine = cards_of_type(PACK_160, "wine")
    assert len(wine) >= 2, "军争 160 档必有两张【酒】"
    hand = (
        _suited_hand_for(PACK_160)
        + (wine[0], wine[1])
        + tuple(deck_excluding(PACK, *_suited_hand_for(PACK_160), *wine[:2])[:1])
    )
    state = build_state(
        pack=PACK_160,
        seats=[
            SeatSpec(hp=1, hp_max=4, hand=hand, hero=JIE_LVMENG, identity=Identity.LORD),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    driver = _BotuDriver(keep=wine, play_wine=True, top_up=False)
    engine = Engine.from_state(state, seed=7)
    driver.engine = engine
    return engine, driver


def test_turn_end_cleanup_lands_on_the_extra_turn_boundary():
    """【酒】的加伤与"每回合一次"计数在**每个**回合末清、额外回合里因此能再用一张（验收 2）。

    序列：自然回合出【酒】①（加伤置位）→ 回合末清 → 额外回合起点再出【酒】②（加伤再次
    置位，说明**每回合一次**的计数也被清了）→ 额外回合末再清。记录流里因此是
    ``wine_change = true / false / true / false``——两条 ``false`` 分别是两个回合的收尾。
    """
    engine, driver = _wine_engine()
    run_until(engine, [driver, PassPlayer(), PassPlayer(), PassPlayer()],
              lambda e: len(_turn_starts(e)) >= 3)

    changes = [
        (record.seq, record.active)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "wine_change"
    ]
    assert [active for _seq, active in changes] == [True, False, True, False], (
        f"两个回合各用一张【酒】，各自由自己的回合末清理置假：{changes}"
    )
    starts = [
        record.seq
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_START
    ]
    assert _turn_starts(engine)[:3] == [0, 0, 1], "第二个回合是额外回合，之后才轮到自然下家"
    assert changes[0][0] < changes[1][0] < starts[1] < changes[2][0], (
        "清理落在自然回合收尾、额外回合起点之前；第二次置位在额外回合里"
    )


# ---------------------------------------------------------------------------
# ⑤ 判据的"所有来源"口径（`06`（§5「程序化约定与偏离一手」） 的裁定）：任何一次置入弃牌堆都算
# ---------------------------------------------------------------------------


def test_every_source_zone_that_feeds_the_discard_pile_counts_for_the_four_suits():
    """四花色判据读**搬牌原语**：手牌 / 装备区 / 判定区 / 牌堆任一来源都算，与原因无关。

    这一条钉的是裁定本身（"计入本回合所有来源置入的牌"）：判据若只在弃牌阶段的调用点
    记账，阵亡清区 / 主公杀忠臣的弃置 / 重铸 / 判定牌这些来源就全被漏掉。
    """
    from sgs.effects import EffectContext, move_card
    from sgs.state import MoveReason, ZoneRef

    hand, other = _suited_hand()[:2], _suited_hand()[2:]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=hand, equip=(other[0],), judge=(other[1],)),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, *hand, *other),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    ctx = EffectContext(actor=0)
    move_card(
        state, ctx, state.refs(hand),
        to_zone=ZoneRef(Zone.DISCARD), reason=MoveReason.DISCARD, owner=0,
    )
    assert len(state.discard_suits_this_turn) == 2

    move_card(
        state, ctx, state.refs((other[0],)),
        to_zone=ZoneRef(Zone.DISCARD), reason=MoveReason.LOSE, owner=0,
    )
    move_card(
        state, ctx, state.refs((other[1],)),
        to_zone=ZoneRef(Zone.DISCARD), reason=MoveReason.USE, owner=None,
    )
    assert sorted(state.discard_suits_this_turn) == sorted(SUITS), "四种花色齐了，与来源 / 原因无关"

    # 花色按首次出现序去重：再往弃牌堆放同花色的牌不会重复记。
    move_card(
        state, ctx, state.refs((hand[0],)),
        to_zone=ZoneRef(Zone.HAND, 1), reason=MoveReason.DRAW, owner=1,
    )
    move_card(
        state, ctx, state.refs((hand[0],)),
        to_zone=ZoneRef(Zone.DISCARD), reason=MoveReason.DISCARD, owner=0,
    )
    assert len(state.discard_suits_this_turn) == 4


# ---------------------------------------------------------------------------
# ⑥ 可回放：额外回合 / 轮次边界由记录流自己说清楚（spec §投影与记录流）
# ---------------------------------------------------------------------------


def test_the_extra_turn_and_round_boundaries_are_replayable_from_the_tape(tmp_path):
    """磁带重放逐字节复现：额外回合从"一次技能发动"重放出来，``turn_change`` 一字不改。

    记录流里的回合边界不是"计划"而是**事实**（额外回合真的开始了 / 轮次真的 +1），
    故它只由状态与动作重放决定——这条判据把它钉在字节层（与 ``tests/test_replays.py``
    的基准重放同一条口径）。
    """
    from sgs.agents import TapeError, TapePlayer, tape_from_journal
    from sgs.engine import dump_journal, read_journal

    # ``top_up=False``：本局不走"测试脚本直接搬牌"的加速路径（那类命令不在磁带里，
    # 重放不会重现它们）——判据要的是**记录流自己**能重放。
    engine, driver = _botu_engine(top_up=False)
    run_until(engine, [driver, PassPlayer()], lambda e: len(_turn_starts(e)) >= 4)
    journal = engine.journal()
    path = tmp_path / "botu.jsonl"
    path.write_text(journal, encoding="utf-8")
    seed, tape = tape_from_journal(read_journal(path))

    replayed = Engine.from_state(_botu_state(), seed=seed)
    player = TapePlayer(tape)
    with pytest.raises(TapeError):
        replayed.run([player, player])

    assert dump_journal(replayed.records) == journal
    changes = [
        (record.round, record.extra, record.seat)
        for record in replayed.records
        if record.kind == "delta" and record.delta_type == "turn_change"
    ]
    assert changes == [(1, True, 0), (2, False, 0)], (
        f"重放出来的记录流里仍是「额外回合 + 第 2 轮」两条边界：{changes}"
    )


# ---------------------------------------------------------------------------
# ⑦ AI：语义表里的正分真的会让启发式 AI 发动【博图】（spec D16 的"AI 真的发动过"）
# ---------------------------------------------------------------------------


def test_the_heuristic_ai_chooses_to_activate_botu():
    """启发式 AI 面对【博图】的"是否发动"答**发动**（spec D16 的"AI 真的发动过"）。

    判据读的是**询问上的应答**（``HeuristicPlayer.respond`` 这一条外部缝），不是 AI 私有
    打分函数：``SKILL_VALUES`` 少了 ``hero.jie_lvmeng.botu`` 时它落在缺省 ``Pass`` 上，
    这条立刻变红（额外的整回合收益远高于一次用牌，正分是唯一合理的默认策略）。
    """
    from sgs.agents import HeuristicPlayer
    from sgs.engine import ai_stream
    from sgs.state import ActivateSkill

    engine, driver = _botu_engine(top_up=False)
    run_until(
        engine,
        [driver, PassPlayer()],
        lambda e: e.pending is not None
        and e.pending.kind == "SKILL_ACTIVATION"
        and str(e.pending.skill_id) == JIE_BOTU,
    )
    assert engine.pending is not None, "本回合弃牌堆已含四花色 ⇒ 必有【博图】的发动询问"

    action = HeuristicPlayer(ai_stream(7, 0)).respond(engine.pending)

    assert isinstance(action, ActivateSkill) and action.skill_id == JIE_BOTU
