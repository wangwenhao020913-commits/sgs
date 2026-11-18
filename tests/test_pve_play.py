"""PvE 玩法面（M7 票 08）：**跑完的局** + 步骤表分叉 + 终局与纯求解器一致。

票 01 §4 的四类逐场条件各一条真跑的局：``ROUT`` / ``SURVIVE_ROUNDS`` /
``ROUT_WITHIN_ROUNDS``（胜与超时判败两条）/ ``SLAY_BOSS``；外加队友局（2v3）与
「受保护队友阵亡」。判据不是"纯函数算得对"（那归票 07 的 ``solve_pve_outcome`` 单测），
而是"引擎在这些时点**真的**调到了它、并且宣布的终局与它一致"：

- **轮次类**（存活 N 轮 / N 轮内全歼）用 ``Engine.new_game`` 起真局：它们只依赖轮次推进，
  与手牌无关，于是连开局发牌一起跑通（``PassPlayer`` 只是不打牌）；
- **阵亡类**（全歼 / 挑 Boss / 保护对象阵亡）用手搓局面 + ``Engine.from_state``：短局要
  **确定地**打出那一刀，只能把"谁手里有什么牌、敌人剩几点体力"钉住；之后每一步都走真引擎
  （使用 → 结算 → 濒死 → 死亡段 → ``check_game_end`` → 终局记录）。

七条局都用 :func:`_assert_matches_the_pure_solver` 收口：**终局记录**（``side`` /
``winners`` / ``reason``）= 拿终局存活集合与轮次重算 ``solve_pve_outcome`` 的答案，且
``game_end`` 是记录流最后一条。这条断言同时钉住"PvE 不另造平局""不写第二个计数源"——
轮次只读 ``GameState.round``。（它是**自洽**检查：真正钉住"判定时机"的是各条用例里
``state.round ==`` 那几行字面断言——判早了 / 判晚了都会与它冲突。）另外三条不适用它：
两条步骤表断言不起局，回合内阵亡那条**停在终局前**（主角还活着）。
"""

from __future__ import annotations

import pytest

from sgs.agents import HeuristicPlayer
from sgs.data import (
    Identity,
    Mode,
    PveConfig,
    PveVictory,
    PveVictoryKind,
    RuleConfig,
    Suit,
    load_content_pack,
)
from sgs.engine import Engine, ai_stream, solve_pve_outcome
from sgs.engine.steps import DEATH_STEPS, IDENTITY_DEATH_STEPS, build_steps
from sgs.state import EndPhase, FrameKind, GameState, UseCard
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, cards_of_type, deck_excluding, run_until

PACK = load_content_pack(deck_profile="standard_108")
ALLY = Identity.ALLY
ENEMY = Identity.ENEMY


def _rule(
    identities: tuple[Identity, ...],
    *,
    victory: PveVictory,
    principal: int = 0,
    protected_ally: int | None = None,
    boss: int | None = None,
    first_seat: int = 0,
) -> RuleConfig:
    """一局 PvE 的规则配置（阵营 / 主角 / 胜负条件都进 header，票 01 §2）。"""
    return RuleConfig(
        player_count=len(identities),
        mode=Mode.PVE,
        first_seat=first_seat,
        pve=PveConfig(
            identities=identities,
            principal=principal,
            protected_ally=protected_ally,
            victory=victory,
            boss=boss,
        ),
    )


class _CardTargetPlayer:
    """出牌阶段打出第一张 ``card_type`` 且目标命中 ``targets``；其余询问走最小合法应答。

    它**不读敌友**（脚本化测试不该复用被测的关系判定）：目标由构造参数钉死，
    于是"谁打谁"是局面设定，而不是 AI 的判断。
    """

    def __init__(self, card_type: str, targets: tuple[int, ...]) -> None:
        self.card_type = card_type
        self.targets = targets
        self._fallback = PassPlayer()

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            for candidate in query.legal_actions:
                if not isinstance(candidate, UseCard):
                    continue
                if candidate.card.type != self.card_type:
                    continue
                if set(candidate.targets) & set(self.targets):
                    return candidate
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        return self._fallback.respond(query)


def _reveal_sides(state: GameState) -> GameState:
    """手搓局面也照真开局置 ``identity_visible``（真局由 ``_initial_player`` 置真）。

    没有这一步，这组用例就跑在"没人看得见阵营"的局面上——与真 PvE 局差一层，
    而"阵营公开"正是票 01 §3 的裁定。
    """
    for player in state.players:
        player.identity_visible = True
    return state


def _assert_matches_the_pure_solver(engine: Engine) -> None:
    """终局记录 = 用终局存活集合与轮次重算的 ``solve_pve_outcome``；且它是最后一条记录。"""
    config = engine.state.rule_config.pve
    assert config is not None
    alive = frozenset(player.seat for player in engine.state.players if player.alive)
    expected = solve_pve_outcome(
        config=config, alive=alive, round_no=engine.state.round
    )
    outcome = engine.pending_outcome
    assert engine.finished and outcome is not None, "局必须真的收掉"
    assert expected is not None, "终局存活集合重算不出胜负 ⇒ 引擎提前/错时收兵"
    assert (outcome.side, outcome.winners, outcome.reason) == (
        expected.side,
        expected.winners,
        expected.reason,
    ), "宣布的终局与纯求解器不一致"
    tail = engine.records[-1]
    assert tail.kind == "game_end", f"终局记录必须是流里最后一条，实得 {tail.kind}"
    assert tail.side == expected.side and tail.winners == expected.winners
    assert tail.reason == expected.reason
    assert tail.check_seq <= tail.seq


# ---------------------------------------------------------------------------
# ROUT：全歼敌人（1v2）。敌人同一批阵亡 ⇒ 第二个死亡检测点就判出全歼。
# ---------------------------------------------------------------------------


def test_a_three_seat_rout_game_ends_when_the_last_enemy_dies():
    barrage = cards_of_type(PACK, "arrow_barrage")[0]
    rule = _rule((ALLY, ENEMY, ENEMY), victory=PveVictory(kind=PveVictoryKind.ROUT))
    state = _reveal_sides(
        build_state(
            pack=PACK,
            rule_config=rule,
            seats=[
                SeatSpec(hp=4, hand=(barrage,), identity=ALLY),
                SeatSpec(hp=1, hp_max=4, identity=ENEMY),
                SeatSpec(hp=1, hp_max=4, identity=ENEMY),
            ],
            deck=deck_excluding(PACK, barrage),
            turn_seat=0,
        )
    )
    engine = Engine.from_state(state, seed=7)
    engine.run(
        [
            _CardTargetPlayer("arrow_barrage", (1, 2)),
            PassPlayer(),
            PassPlayer(),
        ]
    )

    assert engine.pending_outcome is not None
    assert engine.pending_outcome.side == "ally"
    assert engine.pending_outcome.reason == "enemies_routed"
    assert engine.pending_outcome.winners == (0,), "胜方 = 友方全部座位（含阵亡者）"
    assert engine.state.round == 1, "阵亡检测点判，轮次判定不该插手"
    assert not any(player.alive for player in engine.state.players if player.seat != 0)
    assert len(engine.state.player(0).hand) == 2, (
        "PvE 没有身份局奖惩：手里只剩摸牌阶段那 2 张（走『杀反贼摸 3』的话会多出 3 张）"
    )
    _assert_matches_the_pure_solver(engine)


# ---------------------------------------------------------------------------
# SURVIVE_ROUNDS：轮次推进到 N+1（1v2，全程不打牌）
# ---------------------------------------------------------------------------


def test_a_survive_rounds_game_wins_when_the_round_advances():
    rule = _rule(
        (ALLY, ENEMY, ENEMY),
        victory=PveVictory(kind=PveVictoryKind.SURVIVE_ROUNDS, rounds=1),
    )
    engine = Engine.new_game(seed=1234, rule_config=rule, pack=PACK)

    engine.run([PassPlayer() for _ in range(3)])

    outcome = engine.pending_outcome
    assert engine.state.round == 2, "判点是轮次 +1 之后（N = 1 ⇒ 到 2 才判）"
    assert outcome is not None and outcome.side == "ally"
    assert outcome.reason == "rounds_survived"
    assert all(player.alive for player in engine.state.players)
    _assert_matches_the_pure_solver(engine)


# ---------------------------------------------------------------------------
# ROUT_WITHIN_ROUNDS：N 轮内全歼（胜）与跨过 N 仍有敌人（败）
# ---------------------------------------------------------------------------


def test_a_rout_within_rounds_game_wins_inside_the_limit():
    barrage = cards_of_type(PACK, "arrow_barrage")[0]
    rule = _rule(
        (ALLY, ENEMY, ENEMY),
        victory=PveVictory(kind=PveVictoryKind.ROUT_WITHIN_ROUNDS, rounds=1),
    )
    state = _reveal_sides(
        build_state(
            pack=PACK,
            rule_config=rule,
            seats=[
                SeatSpec(hp=4, hand=(barrage,), identity=ALLY),
                SeatSpec(hp=1, hp_max=4, identity=ENEMY),
                SeatSpec(hp=1, hp_max=4, identity=ENEMY),
            ],
            deck=deck_excluding(PACK, barrage),
            turn_seat=0,
        )
    )
    engine = Engine.from_state(state, seed=7)
    engine.run(
        [
            _CardTargetPlayer("arrow_barrage", (1, 2)),
            PassPlayer(),
            PassPlayer(),
        ]
    )

    outcome = engine.pending_outcome
    assert outcome is not None and outcome.side == "ally"
    assert outcome.reason == "enemies_routed_in_time"
    assert engine.state.round == 1, "在第 1 轮里全歼 ⇒ 胜（硬边界是 N = 1）"
    _assert_matches_the_pure_solver(engine)


def test_a_rout_within_rounds_game_loses_when_the_round_limit_passes():
    """跨过 N 仍有敌人 ⇒ 败（不是"不判"）：这条钉住"先判轮次、后判全歼"的硬边界。"""
    rule = _rule(
        (ALLY, ENEMY, ENEMY),
        victory=PveVictory(kind=PveVictoryKind.ROUT_WITHIN_ROUNDS, rounds=1),
    )
    engine = Engine.new_game(seed=11, rule_config=rule, pack=PACK)

    engine.run([PassPlayer() for _ in range(3)])

    outcome = engine.pending_outcome
    assert engine.state.round == 2
    assert outcome is not None and outcome.side == "enemy"
    assert outcome.reason == "time_limit_exceeded"
    assert outcome.winners == (1, 2), "败方 = 敌方全部座位"
    assert all(player.alive for player in engine.state.players), "没人阵亡也照样判败"
    _assert_matches_the_pure_solver(engine)


# ---------------------------------------------------------------------------
# SLAY_BOSS：1v1 里 Boss 座位阵亡
# ---------------------------------------------------------------------------


def test_a_slay_boss_game_ends_when_the_boss_dies():
    slash = cards_of_type(PACK, "slash")[0]
    rule = _rule(
        (ALLY, ENEMY),
        victory=PveVictory(kind=PveVictoryKind.SLAY_BOSS),
        boss=1,
    )
    state = _reveal_sides(
        build_state(
            pack=PACK,
            rule_config=rule,
            seats=[
                SeatSpec(hp=4, hand=(slash,), identity=ALLY),
                SeatSpec(hp=1, hp_max=4, identity=ENEMY),
            ],
            deck=deck_excluding(PACK, slash),
            turn_seat=0,
        )
    )
    engine = Engine.from_state(state, seed=7)
    engine.run([_CardTargetPlayer("slash", (1,)), PassPlayer()])

    outcome = engine.pending_outcome
    assert outcome is not None and outcome.side == "ally"
    assert outcome.reason == "boss_slain"
    assert outcome.winners == (0,)
    assert engine.state.player(1).alive is False, "Boss 必须真的死了（不是只判了配置）"
    _assert_matches_the_pure_solver(engine)


# ---------------------------------------------------------------------------
# 队友局（2v3）：两个友方座位里有一个是 **AI 队友**，整局由启发式打完
# ---------------------------------------------------------------------------


def test_a_two_ally_three_enemy_game_runs_to_an_end_with_the_teammate_acting():
    rule = _rule(
        (ALLY, ENEMY, ENEMY, ALLY, ENEMY),
        victory=PveVictory(kind=PveVictoryKind.ROUT),
    )
    engine = Engine.new_game(seed=47, rule_config=rule, pack=PACK)

    engine.run([HeuristicPlayer(ai_stream(47, seat)) for seat in range(5)])

    outcome = engine.pending_outcome
    assert outcome is not None and outcome.side == "ally", "队友局：友方全歼敌人"
    assert outcome.winners == (0, 3), "胜方含 AI 队友座位（不是只有主角）"
    turns = [
        record.actor
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_START
    ]
    assert turns.count(0) >= 1 and turns.count(3) >= 1, (
        f"主角与队友都要真的行动过（回合序：{turns[:8]}…）"
    )
    assert all(player.identity_visible for player in engine.state.players)
    _assert_matches_the_pure_solver(engine)


# ---------------------------------------------------------------------------
# 受保护队友阵亡（2v2）：主角还活着，但这一场判败
#
# 票面写「1v2 带 protected_ally」：``protected_ally`` 必须是**第二个友方座位**
# （``PveConfig`` 的构造期校验），故最小合法局面是 2 友 × 2 敌；"1 友 + 保护对象"
# 在 3 座里放不下。这条就地收窄，理由记在票 08 的 ``## Answer``。
# ---------------------------------------------------------------------------


def test_the_protected_allys_death_loses_the_game_even_though_the_principal_lives():
    slash = cards_of_type(PACK, "slash")[0]
    rule = _rule(
        (ALLY, ALLY, ENEMY, ENEMY),
        principal=0,
        protected_ally=1,
        victory=PveVictory(kind=PveVictoryKind.ROUT),
    )
    state = _reveal_sides(
        build_state(
            pack=PACK,
            rule_config=rule,
            seats=[
                SeatSpec(hp=4, identity=ALLY),
                SeatSpec(hp=1, hp_max=4, identity=ALLY),
                SeatSpec(hp=4, hand=(slash,), identity=ENEMY),
                SeatSpec(hp=4, identity=ENEMY),
            ],
            deck=deck_excluding(PACK, slash),
            turn_seat=0,
        )
    )
    engine = Engine.from_state(state, seed=7)
    engine.run(
        [
            PassPlayer(),
            PassPlayer(),
            _CardTargetPlayer("slash", (1,)),
            PassPlayer(),
        ]
    )

    outcome = engine.pending_outcome
    assert outcome is not None and outcome.side == "enemy"
    assert outcome.reason == "protected_ally_dead"
    assert outcome.winners == (2, 3)
    assert engine.state.player(0).alive is True, "主角还活着 ⇒ 判败来自受保护队友"
    assert engine.state.player(1).alive is False
    _assert_matches_the_pure_solver(engine)


# ---------------------------------------------------------------------------
# 步骤表分叉：PvE 与无身份局**同表**、与身份局**不同表**（票 01 §5）
# ---------------------------------------------------------------------------


def test_the_pve_death_table_is_the_no_identity_one():
    pve = build_steps(Mode.PVE)
    plain = build_steps(Mode.NO_IDENTITY)
    identity = build_steps(Mode.IDENTITY)

    assert pve == plain, "PvE 与无身份局共用 DEATH_STEPS"
    assert pve[FrameKind.DEATH] is DEATH_STEPS
    assert identity[FrameKind.DEATH] is IDENTITY_DEATH_STEPS
    assert pve != identity, "身份局那段「亮出身份牌前」PvE 没有"

    runs = [step.run for step in pve[FrameKind.DEATH]]
    assert len(runs) == len(DEATH_STEPS)
    assert not any(
        step.run.__name__ == "death_reveal_identity_before" for step in pve[FrameKind.DEATH]
    ), "PvE 不亮身份（开局已全部公开）"
    assert any(
        step.run.__name__ == "death_reveal_identity_after"
        for step in pve[FrameKind.DEATH]
    ), "胜负检测点仍在这一段里（主角 / Boss 阵亡就判）"


def test_a_mode_without_a_death_table_entry_goes_red_instead_of_defaulting():
    """分叉是**按模式登记**的 fail-closed 表，不是 ``if/else`` 兜底。

    未来加第四个 ``Mode`` 时，忘了登记 DEATH 帧就直接 ``KeyError``——"碰巧复用无身份局那张
    表"这种静默行为在这里被堵住（票 08 的验收：分叉本身要有断言）。
    """
    with pytest.raises(KeyError):
        build_steps("some_future_mode")  # type: ignore[arg-type]


def test_a_pve_seat_that_dies_on_its_own_turn_still_ends_that_turn():
    """PvE 的「回合内阵亡 ⇒ 结束其回合」与身份局同形：`TURN_END` 恰一条，随后才 `TURN_AFTER_END`。

    触发面：**非主角 / 非保护对象**的友方座位在自己回合里被【闪电】打死（2v2 可达；1v1
    首死即终局，故无身份局此前走不到这条）。``death_before_start`` 的 non-IDENTITY 早退
    曾把「结束其回合」与「主公锚点」一起挡掉 ⇒ 记录流只剩 ``TURN_AFTER_END``（票 08 的
    Spec 评审实测发现；票 01 §3 那句"现有的 non-IDENTITY 早退已经覆盖"只对主公锚点成立）。
    """
    lightning = cards_of_type(PACK, "lightning")[0]
    judge_card = next(
        card_id
        for card_id, card in enumerate(PACK.deck.cards)
        if card.suit is Suit.SPADE and 2 <= card.rank <= 9
    )
    rule = _rule(
        (ALLY, ALLY, ENEMY, ENEMY),
        principal=0,
        victory=PveVictory(kind=PveVictoryKind.ROUT),
        first_seat=1,
    )
    state = _reveal_sides(
        build_state(
            pack=PACK,
            rule_config=rule,
            seats=[
                SeatSpec(hp=4, identity=ALLY),
                SeatSpec(hp=1, hp_max=4, judge=(lightning,), identity=ALLY),
                SeatSpec(hp=4, identity=ENEMY),
                SeatSpec(hp=4, identity=ENEMY),
            ],
            deck=(judge_card,) + deck_excluding(PACK, lightning, judge_card),
            turn_seat=1,
        )
    )
    engine = Engine.from_state(state, seed=7)

    def _seat_one_turn_is_over(candidate: Engine) -> bool:
        return any(
            record.kind == "timing"
            and record.timing is Timing.TURN_AFTER_END
            and record.actor == 1
            for record in candidate.records
        )

    run_until(engine, [PassPlayer() for _ in range(4)], _seat_one_turn_is_over)

    assert engine.state.player(1).alive is False, "闪电必须真的打死座位 1"
    assert not engine.finished, "主角还活着 ⇒ 这一场继续（不是终局）"
    marks = [
        (record.timing, record.actor)
        for record in engine.records
        if record.kind == "timing"
        and record.actor == 1
        and record.timing
        in (Timing.DEATH_BEFORE_START, Timing.TURN_END, Timing.TURN_AFTER_END)
    ]
    assert marks == [
        (Timing.DEATH_BEFORE_START, 1),
        (Timing.TURN_END, 1),
        (Timing.TURN_AFTER_END, 1),
    ], "顺序就是 死亡结算开始前 → TURN_END（恰一条）→ 跳阶段后的 TURN_AFTER_END"

    # **终局那一死**也发 TURN_END（主角在自己回合里阵亡 ⇒ 这一场就此结束）。
    # 与身份局主公锚点的差别是**判点位置**：身份局的"主公死亡 ⇒ 游戏结束"读的是死者身份、
    # 就在 DEATH_BEFORE_START 让位；PvE 的胜负在死亡检测点（`death_reveal_identity_after`）
    # 才判，`death_before_start` 这里只知道"这一回合真的结束了" ⇒ 照发 TURN_END、随后直接
    # 落 `game_end`（没有 `TURN_AFTER_END`）。这条记录面会被票 12 的 PvE 基准固化，故显式钉住。
    principal_rule = _rule(
        (ALLY, ENEMY, ENEMY),
        principal=0,
        victory=PveVictory(kind=PveVictoryKind.ROUT),
        first_seat=0,
    )
    principal_state = _reveal_sides(
        build_state(
            pack=PACK,
            rule_config=principal_rule,
            seats=[
                SeatSpec(hp=1, hp_max=4, judge=(lightning,), identity=ALLY),
                SeatSpec(hp=4, identity=ENEMY),
                SeatSpec(hp=4, identity=ENEMY),
            ],
            deck=(judge_card,) + deck_excluding(PACK, lightning, judge_card),
            turn_seat=0,
        )
    )
    principal_engine = Engine.from_state(principal_state, seed=7)
    principal_engine.run([PassPlayer() for _ in range(3)])

    assert principal_engine.pending_outcome is not None
    assert principal_engine.pending_outcome.reason == "principal_dead"
    principal_marks = [
        record.timing
        for record in principal_engine.records
        if record.kind == "timing"
        and record.actor == 0
        and record.timing in (Timing.TURN_END, Timing.TURN_AFTER_END)
    ]
    assert principal_marks == [Timing.TURN_END], (
        "终局死亡：只发 TURN_END（不再有 TURN_AFTER_END），终局记录紧接在后"
    )
    assert principal_engine.records[-1].kind == "game_end"
