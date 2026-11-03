"""J 族：可重复的判定循环（M6 票 02）——甄姬【洛神】。

规则原文：§九 L979–981（"准备阶段开始时，你可以进行判定，若结果为黑色，你获得此牌，
然后可以重复此流程，直到出现红色结果为止"）。

票面把这条"可重复"的循环**定死**在这里，落点全部是外部缝：

- **询问缝**：``SKILL_ACTIVATION``（每次发动问一次）与 ``SELECT_OPTION``
  （``skill_id = luoshen:repeat:<轮号>``：**每轮**问一次"是否继续"）；
- **记录流**：每轮一个独立的 ``JUDGE`` 帧 + 一条 ``JUDGE_ON`` 时机记录 ⇒ "又一轮"
  在记录流里就是"又一个判定帧"，回放按同一串决策重跑；
- **终局状态**：判定牌的去向（黑 ⇒ 进手牌、红 ⇒ 进弃牌堆）、牌堆顶的推进。

"直到出现红色结果为止"是**终止条件**：红色那一轮**不**问"是否继续"（下一条判定不可能
发生）；黑色那一轮问，答不继续就停（中途放弃）——两种出口因此各有各的用例。
"""

from __future__ import annotations

from sgs.data import Color, RuleConfig, color_of_suit, load_content_pack
from sgs.engine import Engine
from sgs.state import EndPhase, FrameKind, Pass, QueryKind, SelectOption
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, run_until

PACK = load_content_pack(deck_profile="standard_108")

LUOSHEN = "hero.zhenji.luoshen"
REPEAT_PREFIX = "luoshen:repeat:"


BLACKS = tuple(
    sorted(
        index
        for index, card in enumerate(PACK.deck.cards)
        if color_of_suit(card.suit) is Color.BLACK
    )
)
REDS = tuple(
    sorted(
        index
        for index, card in enumerate(PACK.deck.cards)
        if color_of_suit(card.suit) is Color.RED
    )
)


class _LuoshenPlayer:
    """座位 0 的脚本：发动洛神、按 ``repeats`` 决定每轮答「继续 / 停止」。

    ``repeats`` 是**答"继续"的上限**：第 ``repeats + 1`` 次"是否继续"答停止；
    ``None`` = 永远继续（终止只能来自红色判定）。
    """

    def __init__(self, *, activate: bool = True, repeats: int | None = None) -> None:
        self.activate = activate
        self.repeats = repeats
        self.activation_queries: list = []
        self.repeat_queries: list = []

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == LUOSHEN:
            self.activation_queries.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == QueryKind.SELECT_OPTION and str(query.skill_id).startswith(
            REPEAT_PREFIX
        ):
            self.repeat_queries.append(query)
            repeat = self.repeats is None or len(self.repeat_queries) <= self.repeats
            if not repeat:
                # B1：停止 = 右端「放弃」= 候选里的 Pass（不再是 stop 选项）。
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == "repeat":
                    return action
            raise AssertionError(f"「是否继续」里没有 repeat：{query.legal_actions}")
        if query.kind == QueryKind.PLAY_PHASE:
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _engine(
    deck: tuple[int, ...],
    *,
    hand: tuple[int, ...] = (),
    turn_seat: int = 0,
    seats: int = 2,
):
    """甄姬在座位 0 的局面；牌堆序由调用方给定（判定逐张消耗牌堆顶）。"""
    specs = [SeatSpec(hand=hand, hero="zhenji"), *(SeatSpec() for _ in range(seats - 1))]
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=deck,
        turn_seat=turn_seat,
        rule_config=RuleConfig(player_count=seats, first_seat=turn_seat),
    )
    return Engine.from_state(state, seed=7)


def _to_play_phase(engine, player):
    """推进到**座位 0 的出牌阶段**（准备 / 判定 / 摸牌阶段都已经过去）。"""
    run_until(
        engine,
        [player, *(PassPlayer() for _ in range(len(engine.state.players) - 1))],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    return engine


def _judgment_count(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.JUDGE_ON
    )


def _judge_frames(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.JUDGE
    )


def test_luoshen_repeat_is_a_single_optional_continue_choice():
    """B1：黑牌后的「是否继续」= 一个 `repeat` 选项 + 一条 `Pass`（放弃 = 停止）。

    前端不再画 repeat / stop 两个按钮：提交 = 唯一选项，放弃 = `Pass`。
    """
    first, second, third = BLACKS[:3]
    red = REDS[0]
    filler = [index for index in range(len(PACK.deck.cards)) if index not in {first, second, third, red}][:2]
    player = _LuoshenPlayer(repeats=1)
    engine = _to_play_phase(_engine((first, second, third, red, *filler)), player)

    assert len(player.repeat_queries) == 2, "两张黑牌各问一次「是否继续」"
    for query in player.repeat_queries:
        assert query.options == ("repeat",), "只剩继续一项；stop 不再是选项"
        choices = [a.choice for a in query.legal_actions if isinstance(a, SelectOption)]
        assert choices == ["repeat"], "候选里只有一个 SelectOption"
        passes = [a for a in query.legal_actions if isinstance(a, Pass)]
        assert len(passes) == 1, "放弃 = Pass（唯一停止出口）"
    assert _judgment_count(engine) == 2, "第一轮继续、第二轮 Pass 停止"


def test_luoshen_keeps_judging_while_the_results_are_black():
    """连黑：黑黑黑红 ⇒ 四轮判定、前三张进手牌、红牌进弃牌堆，且不问第四轮"是否继续"。"""
    first, second, third = BLACKS[:3]
    red = REDS[0]
    filler = [index for index in range(len(PACK.deck.cards)) if index not in {first, second, third, red}][:2]
    player = _LuoshenPlayer(repeats=None)
    engine = _to_play_phase(
        _engine((first, second, third, red, *filler)), player
    )

    assert _judgment_count(engine) == 4, "黑黑黑红 = 四轮判定（红那轮终止）"
    assert _judge_frames(engine) == 4, "每轮是一个独立的 JUDGE 帧（记录流里的「又一轮」）"
    assert len(player.repeat_queries) == 3, "只有黑色那三轮问「是否继续」"
    assert [query.skill_id for query in player.repeat_queries] == [
        f"{REPEAT_PREFIX}0",
        f"{REPEAT_PREFIX}1",
        f"{REPEAT_PREFIX}2",
    ], "每轮的询问标识带轮号（答案缓存不撞车）"
    assert engine.state.player(0).hand == [first, second, third, *filler], (
        "三张黑判定牌按判定顺序进手牌，随后是摸牌阶段的两张"
    )
    assert engine.state.discard == [red], "红判定牌没有被获得，进了弃牌堆"


def test_luoshen_stops_on_the_first_red_judgment():
    """连红：第一张就是红 ⇒ 只判一次、不问「是否继续」、一张都不获得。"""
    red = REDS[0]
    filler = [index for index in range(len(PACK.deck.cards)) if index not in {red}][:2]
    player = _LuoshenPlayer(repeats=None)
    engine = _to_play_phase(_engine((red, *filler)), player)

    assert _judgment_count(engine) == 1
    assert player.repeat_queries == [], "红色即终止：不问「是否继续」"
    assert engine.state.player(0).hand == filler, "只摸到摸牌阶段的两张"
    assert engine.state.discard == [red]


def test_luoshen_stops_when_the_owner_declines_to_repeat():
    """中途放弃：黑黑之后答停止 ⇒ 两轮判定，第三张黑牌**没有**被判定 / 获得。"""
    first, second, third = BLACKS[:3]
    red = REDS[0]
    filler = [index for index in range(len(PACK.deck.cards)) if index not in {first, second, third, red}][:2]
    player = _LuoshenPlayer(repeats=1)
    engine = _to_play_phase(_engine((first, second, third, red, *filler)), player)

    assert _judgment_count(engine) == 2, "答停止后不再压判定帧"
    assert len(player.repeat_queries) == 2, "两轮各问一次「是否继续」"
    assert engine.state.player(0).hand == [first, second, third, red], (
        "前两张黑判定牌进手牌；摸牌阶段摸到的是没被判定过的第三张黑与红"
    )
    assert engine.state.discard == []


def test_declining_luoshen_skips_the_judgment_entirely():
    """反例：不发动 ⇒ 零判定、零「是否继续」询问。"""
    first = BLACKS[0]
    player = _LuoshenPlayer(activate=False)
    engine = _to_play_phase(_engine((first, *BLACKS[1:])), player)

    assert len(player.activation_queries) == 1, "问过一次「是否发动」"
    assert _judgment_count(engine) == 0
    assert player.repeat_queries == []


def test_luoshen_is_not_offered_during_someone_elses_prepare_phase():
    """反例：别人的准备阶段里，甄姬连「是否发动」都不问（available=False）。"""
    first = BLACKS[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="zhenji"), SeatSpec()],
        deck=(first, *BLACKS[1:]),
        turn_seat=1,
        rule_config=RuleConfig(player_count=2, first_seat=1),
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoshenPlayer(repeats=None)
    run_until(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 1,
    )

    assert _judgment_count(engine) == 0, "不是自己的回合 ⇒ 洛神不发动"


def test_luoshen_rounds_are_replayable_from_the_record_stream():
    """可回放：同一局面 + 同一脚本跑两遍，记录流逐字节相等（每轮判定帧都自带决策）。"""
    first, second, third = BLACKS[:3]
    red = REDS[0]
    filler = [index for index in range(len(PACK.deck.cards)) if index not in {first, second, third, red}][:2]
    deck = (first, second, third, red, *filler)

    journals = []
    for _ in range(2):
        player = _LuoshenPlayer(repeats=None)
        engine = _to_play_phase(_engine(deck), player)
        journals.append(engine.journal())

    assert journals[0] == journals[1], "记录流必须可回放出同一局"


def test_luoshen_only_runs_during_its_owners_prepare_phase():
    """形态：洛神的钩子只登记在 ``PHASE_PREPARE`` 上（不是开始 / 结束节点）。"""
    from sgs.skills import CATALOG

    from sgs.timing import DISPATCH_WHITELIST

    definition = CATALOG[LUOSHEN]

    assert definition.locked is False, "技能面写了「你可以」"
    assert [hook.timing for hook in definition.hooks] == [Timing.PHASE_PREPARE]
    assert all(hook.timing in DISPATCH_WHITELIST for hook in definition.hooks)
    assert definition.hooks[0].flow is None, "可重复循环是手写的帧内游标（步骤语言没有循环）"
