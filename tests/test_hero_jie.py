"""M6 界将：界关羽 / 界马超（票 04）+ 界张飞（票 05）。

票 04 的两名界将是**技能三态**（获得 / 失去 / 失效）的引擎原语第一次真用上：

- 【武圣】②（界关羽）：方块【杀】无距离限制 ⇒ ``TARGET_POOL`` 的连续型效果；
- 【义绝】（界关羽）：弃一张牌 → 目标展示一张手牌 → 红：获得并可令其回复 / 黑：**本回合**
  其非锁定技失效 + 不能使用或打出手牌 + 你使用红桃【杀】对其伤害 +1；
- 【马术】（界马超）：与标准版逐字相同（``DISTANCE`` −1），界版只是另一个武将 id；
- 【铁骑】（界马超）：使用【杀】指定目标后 → 令其非锁定技失效 → 判定 → **由目标决定**
  是否弃一张同花色牌；不弃（或没有）⇒ 该目标不能使用【闪】响应此【杀】。

票 05 的界张飞是**限定技**（整局一次）的第一个载体：

- 【替身】（限定技）：准备阶段 → 体力回复至上限 → 摸以此法回复的体力值数张牌；
  第二次询问不再出现，"已用"在回放后一致；
- 【咆哮】①：与标准版逐字相同（无次数限制）；②：被【闪】抵消后，本回合**第一个**
  【杀】伤害事件 +1（"造成伤害时"消费，不是"使用【杀】时"）。

本文件只走外部缝：候选 / 询问序列、记录流（delta）、终局状态（手牌 / 体力 / 距离 / 目标池）。
引擎级的三态形状（获得 / 失去的零载体构造）住 ``tests/test_hero_skill_state.py``。

三条**裁定**（`06` §5「程序化约定与偏离一手」逐条登记；票 05 的账本行由票 21 写回）：

1. 【义绝】"不能使用或打出手牌"**也挡住响应**（【闪】/【桃】）——牌面写的是"使用或打出"，
   响应就是打出；故被义绝者的【闪】候选整个消失（反例：没有义绝时【闪】照常出现）。
2. 【铁骑】"除非该角色弃置一张与判定结果花色相同的牌"——**弃不弃由目标决定**：这一问的
   应答者是目标（``SELECT_CARDS`` 的 ``actor``），``Pass`` = 不弃 = 不能响应。
3. 【咆哮】②"下一次"= 触发后**第一个**由自己使用的【杀】造成的**伤害事件**：被抵消 /
   未造成伤害的那张不消费（与【酒】的使用即消费相反），多目标【杀】只有第一个目标的本次
   伤害 +1；作用域 = 本回合，回合末清掉。
"""

from __future__ import annotations

from sgs.agents import tape_from_journal
from sgs.data import Color, RuleConfig, Suit, color_of_suit, load_content_pack
from sgs.effects import behavior_for, check_targets, distance, legal_actions_for
from sgs.engine import Engine
from sgs.skills import (
    JIE_MASHU,
    JIE_TIEQI,
    YIJUE,
    YIJUE_HEAL,
    YIJUE_HEAL_OPTIONS,
    conversion_offers,
    limited_skill_used,
    prune_skill_state,
    reset_phase_skill_state,
)
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    QueryKind,
    Reforge,
    SelectCards,
    SelectOption,
    UseCard,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    RecordingPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    ran_timing,
    run_queries,
    run_turns,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")

JIE_WUSHENG = "hero.jie_guanyu.wusheng"


# ---------------------------------------------------------------------------
# 脚本参与者与候选 / 询问的小工具（与既有族测试同一姿态）
# ---------------------------------------------------------------------------


class Scripted:
    """先跑 ``handler``，返回 ``None`` 时退到最小应答（结束阶段 / 放弃）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = RecordingPlayer()
        self.queries = []

    def respond(self, query):
        self.queries.append(query)
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _activate(query, skill_id: str):
    return _find(
        query,
        lambda action: isinstance(action, ActivateSkill)
        and action.skill_id == skill_id,
    )


def _seat_option(query, seat: int):
    return _find(
        query,
        lambda action: isinstance(action, SelectOption)
        and action.choice == f"seat:{seat}",
    )


def _option(query, choice: str):
    return _find(
        query,
        lambda action: isinstance(action, SelectOption) and action.choice == choice,
    )


def _slot_of(query, card_id: int) -> int:
    for index, slot in enumerate(query.slots):
        if slot.card is not None and slot.card.card_id == card_id:
            return index
    raise AssertionError(f"选择面里没有牌 {card_id}：{query.slots}")


def _pick(query, card_ids: tuple[int, ...]):
    return SelectCards(
        picks=tuple(_slot_of(query, card_id) for card_id in card_ids),
        answers_seq=query.seq,
    )


def _use_at(query, card_id: int, targets: tuple[int, ...]):
    return _find(
        query,
        lambda action: isinstance(action, UseCard)
        and action.card.card_id == card_id
        and action.targets == targets,
    )


def _decline(query):
    if query.kind in (QueryKind.RESPOND, QueryKind.DODGE, QueryKind.PEACH):
        return _find(query, lambda action: isinstance(action, Pass))
    return None


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _first(*, type_id: str | None = None, color: Color | None = None, suit: str | None = None):
    for card_id, card in enumerate(PACK.deck.cards):
        if type_id is not None and card.type != type_id:
            continue
        if color is not None and color_of_suit(card.suit) is not color:
            continue
        if suit is not None and card.suit != suit:
            continue
        return card_id
    raise AssertionError("夹具里找不到满足条件的牌")


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(
        card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded
    )


def _deltas(engine: Engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == delta_type
    ]


def _queries(engine: Engine, kind: str) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _run(state, players, stop, *, seed: int = 7):
    engine = Engine.from_state(state, seed=seed)
    run_until(engine, players, stop)
    return engine


def _hero_skill_instances(state, seat: int, skill_id: str) -> list:
    from sgs.skills import resolve_instances

    return [
        instance
        for instance in resolve_instances(state)
        if instance.owner == seat and instance.skill_id == skill_id
    ]


# ---------------------------------------------------------------------------
# 界关羽【武圣】②：方块【杀】无距离限制（TARGET_POOL 连续型效果）
# ---------------------------------------------------------------------------


def _wusheng_state(hand: tuple[int, ...]):
    """4 人环：座位 0 = 界关羽（手牌 ``hand``），座位 2 在**攻击范围之外**（距离 2）。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=hand, hero="jie_guanyu"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(*hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )


def test_a_diamond_slash_reaches_beyond_the_attack_range_for_jie_guanyu():
    """正面：方块【杀】的目标池 = 全部其他存活角色（无视"距离 ≤ 攻击范围"）。"""
    diamond_slash = _first(type_id="slash", suit=Suit.DIAMOND.value)
    state = _wusheng_state((diamond_slash,))
    assert distance(state, 0, 2) == 2, "4 人环上座位 0 到 2 的距离 = 2 > 攻击范围 1"

    pool = check_targets(state, 0, state.card(diamond_slash))

    assert 2 in pool, "方块【杀】无距离限制 ⇒ 座位 2 进池"


def test_a_heart_slash_keeps_the_attack_range_limit():
    """反例：花色不是方块 ⇒ 距离判据照旧（无距离限制只属于方块【杀】）。"""
    heart_slash = _first(type_id="slash", suit=Suit.HEART.value)
    state = _wusheng_state((heart_slash,))

    pool = check_targets(state, 0, state.card(heart_slash))

    assert 2 not in pool, "红桃【杀】不享受【武圣】②的无距离限制"
    assert 1 in pool


def test_the_jie_wusheng_still_converts_a_red_card_into_a_slash():
    """①与标准版逐字相同：红牌当【杀】（界关羽的实例上在同一处声明）。"""
    red = _first(type_id="peach", color=Color.RED)
    state = _wusheng_state((red,))

    offers = conversion_offers(state, 0, "slash")

    assert (red, JIE_WUSHENG) in offers


def test_losing_jie_wusheng_removes_the_no_distance_effect_at_once():
    """连续型效果不落状态：失去【武圣】⇒ 目标池立刻回到基线（D9 / 票面验收 2）。"""
    diamond_slash = _first(type_id="slash", suit=Suit.DIAMOND.value)
    state = _wusheng_state((diamond_slash,))
    engine = Engine.from_state(state, seed=1)
    assert 2 in check_targets(state, 0, state.card(diamond_slash))

    engine.lose_skill(0, JIE_WUSHENG)

    assert 2 not in check_targets(state, 0, state.card(diamond_slash)), (
        "实例没了，查询期的目标池修正随之消失"
    )


def test_suppressing_jie_wusheng_also_removes_the_no_distance_effect():
    """失效（实例仍在、被挡）与失去同一条读取路径：效果从**活着的实例**算。"""
    diamond_slash = _first(type_id="slash", suit=Suit.DIAMOND.value)
    state = _wusheng_state((diamond_slash,))
    engine = Engine.from_state(state, seed=1)

    engine.suppress_skills(0, YIJUE)

    assert 2 not in check_targets(state, 0, state.card(diamond_slash))


def test_the_hand_prohibition_blocks_use_and_play_but_not_reforge():
    """裁定边界（`06` §5「程序化约定与偏离一手」）：只挡"使用 / 打出手牌"。

    重铸、弃置、技能发动都不是"使用或打出"，被义绝者照常可以重铸（【铁索连环】）与发动
    技能——否则"不能使用或打出手牌"会顺手禁掉牌面没写的东西。
    """
    pack = load_content_pack(deck_profile="standard_ex_160")
    chain = next(
        card_id
        for card_id, card in enumerate(pack.deck.cards)
        if behavior_for(card).reforgeable
    )
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(chain,), hero="jie_guanyu"),
            SeatSpec(),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id != chain
        ),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=1)
    before = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    assert any(isinstance(action, UseCard) for action in before), "构造：本来能使用这张牌"

    engine.set_hand_card_prohibition(0, True)

    after = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    assert not any(isinstance(action, UseCard) for action in after), "使用手牌被挡"
    assert any(isinstance(action, Reforge) for action in after), "重铸不是使用，照常"


# ---------------------------------------------------------------------------
# 界马超【马术】：与标准版逐字相同（连续型 DISTANCE −1）
# ---------------------------------------------------------------------------


def test_jie_mashu_drops_the_subject_side_distance_by_one():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_machao"), SeatSpec(), SeatSpec(), SeatSpec()],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )

    assert distance(state, 0, 2) == 1, "马术：主体侧 −1（2 → 1）"
    assert distance(state, 2, 0) == 2, "别人算到他的距离不变（方向性）"


def test_losing_jie_mashu_returns_the_distance_to_the_baseline():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_machao"), SeatSpec(), SeatSpec(), SeatSpec()],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    engine = Engine.from_state(state, seed=1)
    assert distance(state, 0, 2) == 1

    engine.lose_skill(0, JIE_MASHU)

    assert distance(state, 0, 2) == 2, "失去【马术】⇒ 距离回到基线"


# ---------------------------------------------------------------------------
# 界关羽【义绝】：三段流水线 + 红 / 黑分叉
# ---------------------------------------------------------------------------


def _yijue_state(
    *,
    owner_hand: tuple[int, ...],
    target_hand: tuple[int, ...],
    target_hp: int = 4,
):
    """两人局：座位 0 = 界关羽、座位 1 = 关羽（有非锁定【武圣】，好观测失效）。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=owner_hand, hero="jie_guanyu"),
            SeatSpec(hand=target_hand, hero="guanyu", hp=target_hp),
        ],
        deck=_deck_excluding(*owner_hand, *target_hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )


def _yijue_player(cost: int, target: int, *, heal: bool, extra_action=None):
    """座位 0 的脚本：发动义绝 → 弃 ``cost`` → 选 ``target`` →（红）选回复 / 不回复。"""

    def handler(query):
        if query.kind == QueryKind.PLAY_PHASE:
            action = _activate(query, YIJUE)
            if action is not None:
                return action
            if extra_action is not None:
                return extra_action(query)
            return _find(query, lambda action: isinstance(action, EndPhase))
        if query.kind == QueryKind.SELECT_CARDS:
            return _pick(query, (cost,))
        if query.kind == QueryKind.SELECT_OPTION:
            if str(query.skill_id).endswith(":seat"):
                return _seat_option(query, target)
            if query.skill_id == "yijue:heal":
                return _option(query, YIJUE_HEAL if heal else "skip")
        return None

    return Scripted(handler)


def _reveal_player(card_id: int):
    """座位 1 的脚本：只应答"展示一张手牌"那一步。"""

    def handler(query):
        if query.kind == QueryKind.SELECT_CARDS:
            return _pick(query, (card_id,))
        return None

    return Scripted(handler)


def test_yijue_black_reveal_suppresses_skills_prohibits_hand_cards_and_boosts_heart_slash():
    """黑色分支：三条效果逐一可观测（失效记录 / 手牌禁令 / 红桃【杀】+1）。"""
    cost = _first(type_id="peach")
    black = _first(type_id="dismantlement", color=Color.BLACK)
    dodge = _first(type_id="dodge")  # 目标手里有【闪】，但本回合它不能打出
    heart_slash = _first(type_id="slash", suit=Suit.HEART.value)
    assert len({cost, black, dodge, heart_slash}) == 4

    state = _yijue_state(
        owner_hand=(cost, heart_slash), target_hand=(black, dodge)
    )
    assert conversion_offers(state, 1, "slash"), "构造保证：目标的红【闪】本来能当【杀】"

    def owner_extra(query):
        # 义绝结算完后，同一个出牌阶段再用红桃【杀】打目标（义绝是"本回合"加伤）。
        return _use_at(query, heart_slash, (1,))

    players = [
        _yijue_player(cost, 1, heal=False, extra_action=owner_extra),
        _reveal_player(black),
    ]
    engine = _run(state, players, lambda e: e.state.player(1).hp == 2)

    # ① 失效：一条座位级记录（施加者 = 义绝），非锁定【武圣】不再产出。
    suppress = _deltas(engine, "skill_suppress")
    assert [(record.seat, record.skill_id, record.suppressed) for record in suppress] == [
        (1, YIJUE, True)
    ]
    assert conversion_offers(state, 1, "slash") == (), "武圣被挡 ⇒ 转化候选消失"
    assert not _hero_skill_instances(state, 1, "hero.guanyu.wusheng")

    # 票面验收 1：失效之后**再获得**的技能也被挡（座位级谓词，不逐技能展开）。
    assert engine.grant_skill(1, "hero.zhaoyun.longdan") is True
    assert not _hero_skill_instances(state, 1, "hero.zhaoyun.longdan"), (
        "后获得的非锁定技同一条记录一并挡住"
    )

    # ② 不能使用或打出手牌：本票裁定——**也挡住响应**，目标的【闪】候选整个消失。
    assert state.player(1).hand_card_prohibited is True
    dodge_queries = _queries(engine, QueryKind.DODGE.value)
    assert dodge_queries, "【杀】仍开【闪】窗口"
    assert all(
        not any(isinstance(action, UseCard) for action in query.legal_actions)
        for query in dodge_queries
    ), "被义绝者连【闪】都不能打出（响应 = 打出）"

    # ③ 红桃【杀】对其伤害 +1：1（基础）+ 1（义绝）= 2。
    assert state.player(1).hp == 2

    # 展示是公开事实（牌留在对方手里：黑色分支不获得）。
    reveals = _deltas(engine, "card_reveal")
    assert [(record.seat, record.card.card_id) for record in reveals] == [(1, black)]
    assert black in state.player(1).hand


def test_yijue_red_gives_the_revealed_card_and_may_heal_the_target():
    """红色分支：你**获得**展示的牌，且**可以**令其回复 1 点体力。"""
    cost = _first(type_id="slash")
    red = _first(type_id="peach", suit=Suit.HEART.value)
    assert cost != red

    state = _yijue_state(
        owner_hand=(cost,), target_hand=(red,), target_hp=3
    )
    players = [_yijue_player(cost, 1, heal=True), _reveal_player(red)]
    engine = _run(state, players, lambda e: e.state.player(1).hp == 4)

    heals = [
        query
        for query in _queries(engine, QueryKind.SELECT_OPTION.value)
        if query.skill_id == "yijue:heal"
    ]
    assert len(heals) == 1, "红色分支只问一次「是否回复」"
    assert tuple(heals[0].options) == YIJUE_HEAL_OPTIONS, "选项闭集由技能定义声明"

    assert red in state.player(0).hand, "获得的牌进了你的手牌"
    assert red not in state.player(1).hand
    assert state.player(1).hp == 4, "回复 1 点（3 → 4）"
    assert not _deltas(engine, "skill_suppress"), "红色分支不产生失效"
    assert state.player(1).hand_card_prohibited is False
    hp_changes = [record for record in _deltas(engine, "hp_change")]
    assert [record.amount for record in hp_changes] == [1]


def test_yijue_red_heal_is_optional():
    """反例："可以令其回复"⇒ 选不回复时只获得牌、体力不动。"""
    cost = _first(type_id="slash")
    red = _first(type_id="peach", suit=Suit.HEART.value)

    state = _yijue_state(owner_hand=(cost,), target_hand=(red,), target_hp=3)
    players = [_yijue_player(cost, 1, heal=False), _reveal_player(red)]
    engine = _run(state, players, lambda e: red in e.state.player(0).hand)

    assert state.player(1).hp == 3, "放弃回复"
    assert not _deltas(engine, "hp_change"), "一次体力变化都没有"


def test_yijue_black_does_not_gain_the_revealed_card():
    """反例（黑色分支）：展示的牌留在原主手里，不获得、不移动。"""
    cost = _first(type_id="slash")
    black = _first(type_id="dismantlement", color=Color.BLACK)

    state = _yijue_state(owner_hand=(cost,), target_hand=(black,))
    players = [_yijue_player(cost, 1, heal=False), _reveal_player(black)]
    _run(
        state,
        players,
        lambda e: any(
            getattr(record, "delta_type", None) == "skill_suppress"
            for record in e.records
        ),
    )

    assert black in state.player(1).hand
    assert black not in state.player(0).hand


# ---------------------------------------------------------------------------
# 界马超【铁骑】：失效 + 判定 + 由目标决定是否弃同花色牌
# ---------------------------------------------------------------------------


def _tieqi_state(
    *,
    target_hand: tuple[int, ...],
    judge_card: int,
    target_hero: str = "guanyu",
):
    """座位 0 = 界马超（一手【杀】在后）、座位 1 = 关羽。

    牌堆顶部三张 = 摸牌阶段的两张 + 判定牌（构造局面靠"摸两张后正好轮到判定牌"定位）。
    """
    slash = _first(type_id="slash", suit=Suit.SPADE.value)
    fillers = [
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in (slash, judge_card, *target_hand)
    ]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="jie_machao"),
            SeatSpec(hand=target_hand, hero=target_hero),
        ],
        deck=(fillers[0], fillers[1], judge_card, *fillers[2:]),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    return state, slash


def _tieqi_owner(slash: int, *, activate: bool = True):
    def handler(query):
        if query.kind == QueryKind.PLAY_PHASE:
            action = _use_at(query, slash, (1,))
            if action is not None:
                return action
            return _find(query, lambda action: isinstance(action, EndPhase))
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_TIEQI:
            if not activate:
                return _find(query, lambda action: isinstance(action, Pass))
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == JIE_TIEQI,
            )
        return None

    return Scripted(handler)


def _tieqi_target(discard: int | None):
    """座位 1 的脚本：被问"弃不弃同花色牌"时选 ``discard``；``None`` = 不弃（Pass）。"""

    def handler(query):
        if query.kind == QueryKind.SELECT_CARDS:
            if discard is None:
                return _find(query, lambda action: isinstance(action, Pass))
            return _pick(query, (discard,))
        return None

    return Scripted(handler)


def _advance(engine, players, stop):
    """逐步推进到 ``stop(engine)`` 成立；可在**两次步骤之间**停（此时 ``pending is None``）。

    与 ``tests/test_hero_response_window.py`` 的 ``_advance`` 同形：``run_until`` 只在
    **询问点**检查停止条件，而"先停到出牌阶段、再给座位换手牌"必须停在 PLAY_START 之后、
    出牌阶段询问**之前**——否则那条询问是用旧手牌构造的。
    """
    for _ in range(20_000):
        if stop(engine):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _park_and_seed(state, slash: int):
    """开到**出牌阶段开始**，再把座位 0 的手牌换成一张【杀】（避开摸牌阶段的随机性）。"""
    engine = Engine.from_state(state, seed=7)
    _advance(
        engine,
        [Scripted(), Scripted()],
        lambda e: ran_timing(e, Timing.PHASE_PLAY_START),
    )
    hand = engine.state.player(0).hand
    hand.clear()
    hand.append(slash)
    return engine


def test_jie_tieqi_suppresses_and_makes_an_unmatched_target_unresponsive():
    """判定花色 = 目标没有的牌 ⇒ 连弃牌询问都不发，直接不能使用【闪】。"""
    heart_judge = _first(type_id="peach", suit=Suit.HEART.value)
    # 【闪】在标准版全是红色；取一张**不是红桃**的【闪】：判定为红桃时它不满足"同花色"，
    # 而它本身仍是一个可用的【闪】——正好用来钉"不能使用【闪】"。
    dodge = _first(type_id="dodge", suit=Suit.DIAMOND.value)
    state, slash = _tieqi_state(target_hand=(dodge,), judge_card=heart_judge)

    engine = _park_and_seed(state, slash)
    assert conversion_offers(engine.state, 1, "slash") != (), "构造：武圣本来可用"

    _advance(
        engine,
        [_tieqi_owner(slash), _tieqi_target(None)],
        lambda e: not e.stack and e.state.player(1).hp == 3,
    )

    # 失效先于判定落地：目标座位拿到一条义绝式记录（这里施加者是铁骑）。
    suppress = _deltas(engine, "skill_suppress")
    assert [(record.seat, record.skill_id) for record in suppress] == [(1, JIE_TIEQI)]
    assert conversion_offers(engine.state, 1, "slash") == ()

    assert engine.state.player(1).hp == 3, "不能使用【闪】⇒ 照常受伤"
    assert _queries(engine, QueryKind.DODGE.value) == [], "没有【闪】窗口"
    assert not _queries(engine, QueryKind.SELECT_CARDS.value), (
        "没有同花色牌 ⇒ 不发没有候选的弃牌询问"
    )


def test_jie_tieqi_lets_the_target_discard_a_matching_card_to_keep_the_dodge_window():
    """弃一张同花色的牌 ⇒ 该目标仍可使用【闪】响应（正例，裁定"弃不弃由目标决定"）。"""
    heart_judge = _first(type_id="peach", suit=Suit.HEART.value)
    heart_card = _first(type_id="dismantlement", suit=Suit.HEART.value)
    dodge = _first(type_id="dodge")
    state, slash = _tieqi_state(target_hand=(heart_card, dodge), judge_card=heart_judge)

    engine = _park_and_seed(state, slash)
    target = _tieqi_target(heart_card)

    def target_with_dodge(query):
        answer = target.respond(query)
        if query.kind == QueryKind.DODGE:
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == dodge,
            )
        return answer

    _advance(
        engine,
        [_tieqi_owner(slash), Scripted(target_with_dodge)],
        lambda e: not e.stack and _queries(e, QueryKind.DODGE.value),
    )

    # 这一问的应答者是**目标**（弃不弃由他决定），不是使用者。
    discard_queries = [
        query
        for query in _queries(engine, QueryKind.SELECT_CARDS.value)
        if query.actor == 1
    ]
    assert discard_queries, "目标被问到弃不弃同花色牌"
    assert all(query.spec.optional for query in discard_queries), "可以 Pass（不弃）"
    assert heart_card in engine.state.discard, "同花色牌被弃置"
    assert engine.state.player(1).hp == 4, "弃了 ⇒ 用【闪】抵消，不受伤"
    assert _deltas(engine, "skill_suppress")[0].skill_id == JIE_TIEQI, "失效仍然生效"


def test_jie_tieqi_target_can_pass_and_then_cannot_use_the_dodge():
    """反例（同一构造的另一侧）：目标选择**不弃** ⇒ 不能使用【闪】，照常受伤。"""
    heart_judge = _first(type_id="peach", suit=Suit.HEART.value)
    heart_card = _first(type_id="dismantlement", suit=Suit.HEART.value)
    dodge = _first(type_id="dodge")
    state, slash = _tieqi_state(target_hand=(heart_card, dodge), judge_card=heart_judge)

    engine = _park_and_seed(state, slash)

    _advance(
        engine,
        [_tieqi_owner(slash), _tieqi_target(None)],
        lambda e: not e.stack and e.state.player(1).hp == 3,
    )

    assert heart_card in engine.state.player(1).hand, "Pass = 不弃"
    assert engine.state.player(1).hp == 3
    assert _queries(engine, QueryKind.DODGE.value) == []


def test_jie_tieqi_suppression_happens_inside_the_use_frame_and_the_frame_still_settles():
    """在飞语义（D10）：失效发生在**已开始的【杀】**帧里，该帧照常结算完毕。

    票面验收 3：失效只影响此后**新产生**的解析——本用例同时钉住两半：正在结算的【杀】
    继续走完（目标掉血），而此后新产生的候选（目标的【武圣】转化）才被挡。
    """
    heart_judge = _first(type_id="peach", suit=Suit.HEART.value)
    # 【闪】在标准版全是红色；取一张**不是红桃**的【闪】：判定为红桃时它不满足"同花色"，
    # 而它本身仍是一个可用的【闪】——正好用来钉"不能使用【闪】"。
    dodge = _first(type_id="dodge", suit=Suit.DIAMOND.value)
    state, slash = _tieqi_state(target_hand=(dodge,), judge_card=heart_judge)

    engine = _park_and_seed(state, slash)

    _advance(
        engine,
        [_tieqi_owner(slash), _tieqi_target(None)],
        lambda e: not e.stack and e.state.player(1).hp == 3,
    )

    asserts = [
        record
        for record in engine.records
        if record.kind == "frame"
        and record.frame_kind.value == "USE"
    ]
    pushes = [record for record in asserts if record.op == "push"]
    pops = [record for record in asserts if record.op == "pop"]
    assert len(pushes) == 1 and len(pops) == 1, "本次【杀】只有一个 USE 帧，且正常出栈"
    suppress = _deltas(engine, "skill_suppress")[0]
    assert suppress.frame_id == pushes[0].frame_id, (
        "失效记录落在**正在结算的 USE 帧**里（在飞语义的观测点）"
    )
    assert engine.state.player(1).hp == 3, "在飞的【杀】结算没有被失效打断"
    assert conversion_offers(engine.state, 1, "slash") == (), "此后新产生的解析才被挡"


# ---------------------------------------------------------------------------
# 界张飞【替身】：限定技（整局一次）——§十九 L2075–L2081、§四 L350–L355
#
# 【替身】= 准备阶段，你可以将体力回复至体力上限，摸以此法回复的体力值数张牌。
# 类型是**限定技**（`SkillType.LIMITED`，规则 §4.12）：整局只能发动一次。它与锁定 / 非锁定
# 正交——本技能的技能面写"你可以"，故仍是**非锁定**（发动要先问"Do you want to"）。
#
# "已用"落 per-instance 的 ``GameState.skill_state``（票 04 建好的"已用"键位），与 A 族
# 的阶段位共用一个位图；出牌阶段复位只清阶段位，整局位保留（票 05 把复位收窄）。
# ---------------------------------------------------------------------------

TISHEN = "hero.jie_zhangfei.tishen"
"""界张飞【替身】的全限定技能 id。"""

JIE_PAOXIAO = "hero.jie_zhangfei.paoxiao"
"""界张飞【咆哮】的全限定技能 id（§十九 L2075–L2078）。"""


def _zhangfei_state(
    *,
    hp: int = 4,
    hand: tuple[int, ...] = (),
    target_hand: tuple[int, ...] = (),
    target_hp: int = 4,
):
    """两人局：座位 0 = 界张飞（体力 ``hp``），座位 1 = 无武将（体力 ``target_hp``）。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=hp, hand=hand, hero="jie_zhangfei"),
            SeatSpec(hp=target_hp, hand=target_hand),
        ],
        deck=_deck_excluding(*hand, *target_hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )


def _tishen_player(*, activate: bool = True):
    """座位 0 的脚本：见到【替身】的"是否发动"就按 ``activate`` 应答，其余走最小应答。"""

    def handler(query):
        if query.kind == QueryKind.SKILL_ACTIVATION.value and query.skill_id == TISHEN:
            if activate:
                return _activate(query, TISHEN)
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    return Scripted(handler)


def _tishen_queries(engine) -> list:
    """记录流里【替身】的"是否发动"询问（按出现序）。"""
    return [
        query
        for query in _queries(engine, QueryKind.SKILL_ACTIVATION.value)
        if query.skill_id == TISHEN
    ]


def test_tishen_heals_to_the_max_and_draws_the_healed_cards():
    """正例：体力 1 ⇒ 回复至上限 4（以此法回复 3），**先摸 3 张**，摸牌阶段再摸 2 张。"""
    engine = Engine.from_state(_zhangfei_state(hp=1), seed=7)
    run_queries(
        engine,
        [_tishen_player(), PassPlayer()],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )

    player = engine.state.player(0)
    assert player.hp == 4, "将体力回复至体力上限"
    assert len(player.hand) == 5, "以此法回复 3 点 ⇒ 摸 3 张；摸牌阶段再摸 2 张"
    assert limited_skill_used(engine.state, 0, TISHEN) is True, "发动后落整局已用位"


def test_tishen_asks_only_once_in_the_whole_game():
    """验收 1：首次可发动；**第二次准备阶段不再出现询问**（整局一次）。

    跑完三个回合（界张飞的第二次准备阶段在第三个回合）断言"询问序列里没有第二次"。
    """
    engine = Engine.from_state(_zhangfei_state(hp=2), seed=7)
    run_turns(engine, [_tishen_player(), PassPlayer()], turns=3)

    assert len(_tishen_queries(engine)) == 1, "整局只发一次「是否发动」询问"
    assert engine.state.player(0).hp == 4, "第一次已回复至上限，第二次若又发动会露馅"


def test_declining_tishen_keeps_it_available():
    """反例（可选性）：答"不发动"⇒ 不落已用位，下一次准备阶段仍会问。"""
    engine = Engine.from_state(_zhangfei_state(hp=2), seed=7)
    run_turns(engine, [_tishen_player(activate=False), PassPlayer()], turns=3)

    assert len(_tishen_queries(engine)) == 2, "放弃不消耗限定技"
    assert engine.state.player(0).hp == 2, "没发动就一点体力都没回"
    assert limited_skill_used(engine.state, 0, TISHEN) is False


def test_a_used_limited_skill_survives_phase_resets_and_suppression():
    """"已用"是**整局**位：出牌阶段复位与技能失效都不能让它复活（票 04 的解析顺序）。"""
    engine = Engine.from_state(_zhangfei_state(hp=2), seed=7)
    run_turns(engine, [_tishen_player(), PassPlayer()], turns=3)
    assert limited_skill_used(engine.state, 0, TISHEN) is True

    reset_phase_skill_state(engine.state)
    assert limited_skill_used(engine.state, 0, TISHEN) is True, (
        "PHASE_PLAY_START 的复位只清阶段位，不碰整局位（票 05 的复位收窄）"
    )

    engine.suppress_skills(0, YIJUE)
    prune_skill_state(engine.state)
    assert limited_skill_used(engine.state, 0, TISHEN) is True, (
        "失效 ≠ 来源消失；暂时失效的实例仍算活着（include_suppressed）"
    )


def test_the_used_state_round_trips_through_a_replay():
    """验收 2：录像重放后实例状态一致——"已用"不是记录流字段，而是同一条动作重建出来的。"""
    initial = _zhangfei_state(hp=2)
    engine = Engine.from_state(initial.model_copy(deep=True), seed=7)
    run_turns(engine, [_tishen_player(), PassPlayer()], turns=3)

    seed, tape = tape_from_journal(engine.records)
    replay = Engine.from_state(initial.model_copy(deep=True), seed=seed)
    for action in tape:
        while replay.pending is None and not replay.finished:
            replay._step()
        assert not replay.finished, "磁带在下一次询问前就用尽了"
        assert action.answers_seq == replay.pending.seq
        replay.submit(action)
    while replay.pending is None and not replay.finished:
        replay._step()

    assert replay.state.skill_state == engine.state.skill_state, "per-instance 状态逐键相等"
    assert limited_skill_used(replay.state, 0, TISHEN) is True
    assert len(_tishen_queries(replay)) == 1
    assert replay.journal() == engine.journal(), "重放的记录流逐字节相等"


# ---------------------------------------------------------------------------
# 界张飞【咆哮】②：被【闪】抵消后，本回合下一次【杀】造成伤害时，此伤害 +1
#
# 【咆哮】是**锁定技**（整条技能没有"你可以"）：①无次数限制沿用标准版同一条
# ``COUNT_LIMIT`` 回调；②是本票的"下一次"状态机：
#
# - **触发**：自己使用的【杀】被【闪】抵消（``SeamKind.SLASH_CANCELED``；
#   【闪】的"使用"由引擎判定，不认牌名）；
# - **消费**：本回合**第一个**由自己使用的【杀】造成的伤害事件；`伤害被抵消 / 未造成伤害
#   的那张【杀】不消费`（与【酒】"使用即消费"相反），非【杀】伤害不消费，
#   多目标【杀】只有第一个目标的本次伤害 +1；
# - **作用域**：本回合末清掉（与【义绝】的红桃【杀】加伤同一收尾点）。
# ---------------------------------------------------------------------------


def _paoxiao_owner(slashes: list[int]):
    """座位 0 的脚本：出牌阶段逐张使用 ``slashes`` 里的【杀】（目标座位 1），用光即结束。"""

    def handler(query):
        if query.kind == QueryKind.PLAY_PHASE.value and slashes:
            action = _use_at(query, slashes[0], (1,))
            if action is not None:
                slashes.pop(0)
                return action
        return None

    return Scripted(handler)


def _one_slash_per_turn(slashes: list[int]):
    """座位 0 的脚本：每个出牌阶段只用一张【杀】（模拟"本回合"的边界）。"""

    state = {"spent": False}

    def handler(query):
        if query.kind == QueryKind.PLAY_PHASE.value:
            if not state["spent"] and slashes:
                action = _use_at(query, slashes[0], (1,))
                if action is not None:
                    state["spent"] = True
                    slashes.pop(0)
                    return action
            state["spent"] = False
            return _find(query, lambda action: isinstance(action, EndPhase))
        if query.kind in (
            QueryKind.RESPOND.value,
            QueryKind.DODGE.value,
            QueryKind.PEACH.value,
        ):
            return _decline(query)
        return None

    return Scripted(handler)


def _dodge_player(*dodges: int):
    """座位 1 的脚本：按序打出给定的【闪】；没有可打 / 其它询问一律放弃。"""

    pending = list(dodges)

    def handler(query):
        if query.kind == QueryKind.DODGE.value and pending:
            card_id = pending[0]
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == card_id,
            )
            if action is not None:
                pending.pop(0)
                return action
        return _decline(query)

    return Scripted(handler)


def test_paoxiao_boosts_the_next_slash_damage_after_a_dodge():
    """正面：第一张【杀】被【闪】抵消 ⇒ 第二张【杀】的伤害 1 + 1 = 2。"""
    slash1, slash2 = _cards("slash")[:2]
    dodge = _cards("dodge")[0]
    engine = Engine.from_state(
        _zhangfei_state(hand=(slash1, slash2), target_hand=(dodge,)), seed=7
    )

    run_until(
        engine,
        [_paoxiao_owner([slash1, slash2]), _dodge_player(dodge)],
        lambda e: e.state.player(1).hp == 2,
    )

    assert engine.state.player(1).hp == 2, "第二张【杀】造成 1 + 1 点伤害"
    assert slash1 in engine.state.discard and slash2 in engine.state.discard, "两张都用了"


def test_without_a_dodge_the_slash_is_plain_damage():
    """反例（触发条件）：没有【闪】抵消 ⇒ 伤害不加（+1 不是无条件常驻）。"""
    slash = _cards("slash")[0]
    engine = Engine.from_state(_zhangfei_state(hand=(slash,)), seed=7)

    run_until(
        engine,
        [_paoxiao_owner([slash]), _dodge_player()],
        lambda e: e.state.player(1).hp == 3,
    )

    assert engine.state.player(1).hp == 3, "普通【杀】1 点伤害"


def test_a_dodged_slash_does_not_consume_the_paoxiao_bonus():
    """消费语义：+1 只在**造成伤害**时消费——被抵消的第二张【杀】不消费它。

    第一张被抵消（挂上 +1）→ 第二张也被抵消（没造成伤害，不消费）→ 第三张命中 ⇒ 2 点。
    若按【酒】的"使用即消费"，第三张只会造成 1 点（本条会红）。
    """
    slash1, slash2, slash3 = _cards("slash")[:3]
    dodge1, dodge2 = _cards("dodge")[:2]
    engine = Engine.from_state(
        _zhangfei_state(
            hand=(slash1, slash2, slash3), target_hand=(dodge1, dodge2)
        ),
        seed=7,
    )

    run_until(
        engine,
        [_paoxiao_owner([slash1, slash2, slash3]), _dodge_player(dodge1, dodge2)],
        lambda e: e.state.player(1).hp == 2,
    )

    assert engine.state.player(1).hp == 2, "第三张【杀】仍带 +1（1 + 1）"


def test_the_paoxiao_bonus_expires_at_the_end_of_the_turn():
    """作用域：+1 是"**本回合**下一次"，回合结束即清（下一回合的【杀】不再加伤）。"""
    slash1, slash2 = _cards("slash")[:2]
    dodge = _cards("dodge")[0]
    engine = Engine.from_state(
        _zhangfei_state(hand=(slash1, slash2), target_hand=(dodge,)), seed=7
    )

    run_turns(
        engine,
        [_one_slash_per_turn([slash1, slash2]), _dodge_player(dodge)],
        turns=3,
    )

    assert engine.state.player(1).hp == 3, "回合 1 被抵消、+1 没用掉；回合 3 的【杀】只造成 1 点"
