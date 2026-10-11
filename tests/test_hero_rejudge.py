"""I 族：改判与判定牌（票 11）。

落点与票 11 §验收一致，全部走**外部缝**：

- ``Engine.from_state`` 的人为局面 + 脚本玩家驱动的**记录流**（``RESPOND`` 询问的
  成因与对象、判定结果、牌的去向）；
- 跨层只读查询 ``rejudge_capable_seats`` / ``rejudge_skill_ids`` / ``REJUDGE_SKILL_IDS``
  与 ``WindowCause.REJUDGE`` 的公开形状。

两条技能的逐字语义（票 11 §要做什么 ③）：

- 司马懿【鬼才】（非锁定）：``JUDGE_CARD_BEFORE_EFFECT`` 的**改判循环窗口**——
  打出一张**手牌**代替判定牌（代替 = 原判定牌进弃牌堆）；
- 郭嘉【天妒】（非锁定）：``JUDGE_CARD_AFTER_EFFECT`` 获得**此判定牌**（最终生效的那张）。

循环窗口的三条纪律（票 11 §要做什么 ② 与规则 11.3）：

- 每次替代后窗口**重开**（新的判定牌成为新的"生效前"）；
- **同一次判定**里每名可改判的角色只有一次响应机会（放弃后不能再响应）；
- 窗口**绑帧**：窗口内若有人死亡，应答者区间按存活重滤。
"""

from __future__ import annotations

from types import MappingProxyType

from sgs.data import Color, color_of_suit, hero_skill_id, load_content_pack
from sgs.engine import Engine
from sgs.effects import CarrierKind
from sgs.skills import (
    CATALOG,
    REJUDGE_SKILL_IDS,
    SkillDef,
    rejudge_capable_seats,
    rejudge_skill_ids,
)
from sgs.state import ActivateSkill, EndPhase, Pass, PlayCard, WindowCause
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_turns

PACK = load_content_pack(deck_profile="standard_108")

GUICAI = hero_skill_id("simayi", "guicai")
TIANDU = hero_skill_id("guojia", "tiandu")
LIANYING = hero_skill_id("luxun", "lianying")

INDULGENCE = cards_of_type(PACK, "indulgence")[0]


# ---------------------------------------------------------------------------
# 构造与读取助手
# ---------------------------------------------------------------------------


def _first_by_color(type_id: str, *, black: bool) -> int:
    wanted = Color.BLACK if black else Color.RED
    return next(
        card_id
        for card_id in cards_of_type(PACK, type_id)
        if color_of_suit(PACK.deck.cards[card_id].suit) is wanted
    )


def _card_id(*, black: bool) -> int:
    """一张黑 / 红牌（判定结果只看颜色，取【杀】的某种颜色即可）。"""
    return _first_by_color("slash", black=black)


def _reds(count: int) -> list[int]:
    """``count`` 张互不相同的红牌（可当手牌，也可当判定牌）。"""
    reds = [
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id != INDULGENCE
        and color_of_suit(PACK.deck.cards[card_id].suit) is Color.RED
    ]
    return reds[:count]


def _deck(first: int, *used: int) -> tuple[int, ...]:
    """``first`` 放第 1 张（判定阶段的判定牌），其余牌接在后面。"""
    excluded = {first, *used}
    return (first, *(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded))


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _queries(engine, kind):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _first_turn(engine):
    """座位 0 的回合记录，直到该回合的 ``TURN_AFTER_END`` 为止。"""
    out = []
    for record in engine.records:
        out.append(record)
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END:
            break
    return out


def _timings(records):
    return [record.timing for record in records if record.kind == "timing"]


def _respond_queries(engine, cause):
    return [
        query for query in _queries(engine, "RESPOND") if getattr(query, "cause", None) is cause
    ]


def _fallback(query):
    if query.kind == "PLAY_PHASE":
        return _find(query, lambda action: isinstance(action, EndPhase)) or query.legal_actions[0]
    if query.kind in ("DODGE", "RESPOND", "PEACH"):
        return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
    return query.legal_actions[0]


class Scripted:
    """一个座位：先跑 ``handler``；返回 ``None`` 时退到最小合法应答（响应一律放弃）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return _fallback(query)


def _activate(query, skill_id: str):
    if query.kind != "SKILL_ACTIVATION" or query.skill_id != skill_id:
        return None
    return _find(query, lambda action: isinstance(action, ActivateSkill))


def _play(query, card_id: int):
    if query.kind != "RESPOND" or query.cause is not WindowCause.REJUDGE:
        return None
    return _find(
        query,
        lambda action: isinstance(action, PlayCard) and action.card.card_id == card_id,
    )


def _judgment_state(*, seats, deck, turn_seat: int = 0):
    """座位 0 的判定阶段带一张【乐不思蜀】；``deck[0]`` 就是判定牌。"""
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=turn_seat,
    )


# ---------------------------------------------------------------------------
# ① 能力来源：REJUDGE_SKILL_IDS 由 CATALOG 派生、座位集合随实例生死变化
# ---------------------------------------------------------------------------


def test_rejudge_skill_ids_are_derived_from_the_skill_definitions():
    """``REJUDGE_SKILL_IDS`` 是 ``rejudge_skill_ids(CATALOG)`` 的快照，不是手抄名单。"""
    assert REJUDGE_SKILL_IDS == rejudge_skill_ids(CATALOG)
    assert GUICAI in REJUDGE_SKILL_IDS, "【鬼才】带改判能力标记"
    assert TIANDU not in REJUDGE_SKILL_IDS, "【天妒】只在生效后拿牌，不是改判能力"

    fake = SkillDef(
        skill_id="hero.fake.ghost",
        locked=False,
        carrier=CarrierKind.HERO,
        rejudge_capable=True,
    )
    injected = MappingProxyType({**CATALOG, fake.skill_id: fake})
    assert rejudge_skill_ids(injected) == REJUDGE_SKILL_IDS | {fake.skill_id}


def test_rejudge_capable_seats_resolves_live_instances_and_drops_the_dead():
    state = _judgment_state(
        seats=[SeatSpec(hero="simayi"), SeatSpec(hero="guojia"), SeatSpec()],
        deck=_deck(_card_id(black=True)),
    )

    assert rejudge_capable_seats(state) == (0,), "只有【鬼才】的持有者进座位集合"
    state.player(0).alive = False
    assert rejudge_capable_seats(state) == (), "武将阵亡 ⇒ 改判来源消失、自动出队"


def test_window_cause_has_the_rejudge_value():
    """``WindowCause`` 闭集只许增：M3 加 ``REJUDGE``（票 11）。"""
    assert WindowCause.REJUDGE.value == "REJUDGE"


# ---------------------------------------------------------------------------
# ② 单人改判：询问弹出、结果按新牌算、原判定牌进弃牌堆
# ---------------------------------------------------------------------------


def test_a_single_rejudge_replaces_the_judge_card_and_changes_the_result():
    """判定为黑 ⇒ 乐不思蜀跳阶段；司马懿改判成红 ⇒ 结果按新牌算（票 11 §验收 1）。"""
    original = _card_id(black=True)
    replacement = _card_id(black=False)
    state = _judgment_state(
        seats=[
            SeatSpec(hp=4, judge=(INDULGENCE,)),
            SeatSpec(hp=4, hero="simayi", hand=(replacement,)),
        ],
        deck=_deck(original, replacement),
    )

    def rejudger(query):
        return _play(query, replacement)

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(), Scripted(rejudger)], turns=1)

    turn = _first_turn(engine)
    window = _respond_queries(engine, WindowCause.REJUDGE)
    assert [query.actor for query in window] == [1], "改判询问弹给有能力的座位"
    assert Timing.PHASE_PLAY_START in _timings(turn), (
        "判定牌生效前被改成红 ⇒ 本回合不跳过出牌阶段"
    )
    assert original in engine.state.discard, "原判定牌进弃牌堆"
    assert replacement in engine.state.discard, "结算结束后新判定牌进弃牌堆"
    assert replacement not in engine.state.player(1).hand, "替代的手牌已离开手牌"


def test_a_pass_leaves_the_original_judge_card_in_effect():
    """放弃改判 ⇒ 结果照原判定牌算（正面对照）。"""
    original = _card_id(black=True)
    replacement = _card_id(black=False)
    state = _judgment_state(
        seats=[
            SeatSpec(hp=4, judge=(INDULGENCE,)),
            SeatSpec(hp=4, hero="simayi", hand=(replacement,)),
        ],
        deck=_deck(original, replacement),
    )

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(), Scripted()], turns=1)

    assert _respond_queries(engine, WindowCause.REJUDGE), "有能力的座位要被问一次"
    assert original in engine.state.discard
    assert Timing.PHASE_PLAY_START not in _timings(_first_turn(engine)), (
        "未改判 ⇒ 黑判定照常跳过本回合的出牌阶段"
    )


# ---------------------------------------------------------------------------
# ③ 循环：替代后窗口重开、另一名能力者可再改；每座位对同一次判定只有一次机会
# ---------------------------------------------------------------------------


def _two_rejudgers(*, turn_seat: int = 0):
    """两名【鬼才】持有者（同一武将不同座位 = 两个实例），各持一张互不相同的红牌。"""
    original = _card_id(black=True)
    first, second = _reds(2)
    state = _judgment_state(
        seats=[
            SeatSpec(hp=4, judge=(INDULGENCE,), hero="simayi", hand=(first,)),
            SeatSpec(hp=4, hero="simayi", hand=(second,)),
        ],
        deck=_deck(original, first, second),
        turn_seat=turn_seat,
    )
    return original, first, second, state


def test_the_window_reopens_for_the_other_capable_seat_after_a_substitution():
    """座位 0 改判后窗口重开，座位 1 可再改（票 11 §验收 2）。"""
    original, first, second, state = _two_rejudgers()

    def replays(query):
        return _play(query, first) or _play(query, second)

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(replays), Scripted(replays)], turns=1)

    window = _respond_queries(engine, WindowCause.REJUDGE)
    assert [query.actor for query in window] == [0, 1], "逆时针、替代后重开"
    assert original in engine.state.discard, "被替代掉的原判定牌进弃牌堆"
    assert first in engine.state.discard, "第一次替代的牌也被第二次替代掉"
    assert second in engine.state.discard, "最终生效的牌在结算结束后入弃牌堆"


def test_a_seat_that_passed_is_not_asked_again_after_another_substitutes():
    """放弃后不能再响应：座位 0 放弃、座位 1 替代后，窗口重开也不再问座位 0（规则 11.3）。"""
    original, _first, second, state = _two_rejudgers()

    def second_only(query):
        return _play(query, second)

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(), Scripted(second_only)], turns=1)

    window = _respond_queries(engine, WindowCause.REJUDGE)
    assert [query.actor for query in window] == [0, 1], "座位 0 放弃后不再被问第二次"
    assert original in engine.state.discard, "座位 1 替代 ⇒ 原判定牌进弃牌堆"


def test_a_seat_is_asked_at_most_once_per_judgment():
    """同一座位改判过一次后不再被问第二次（票 11 §验收 3）。"""
    original = _card_id(black=True)
    first, second = _reds(2)
    state = _judgment_state(
        seats=[
            SeatSpec(hp=4, judge=(INDULGENCE,), hero="simayi", hand=(first, second)),
            SeatSpec(hp=4),
        ],
        deck=_deck(original, first, second),
    )

    def rejudger(query):
        return _play(query, first) or _play(query, second)

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(rejudger), Scripted()], turns=1)

    window = _respond_queries(engine, WindowCause.REJUDGE)
    assert [query.actor for query in window] == [0], "只有一个能力者 ⇒ 只问一次，不因循环重问"
    assert second in engine.state.player(0).hand, "第二轮没有把第二张也打掉"


# ---------------------------------------------------------------------------
# ④ 无能力者：队列恒空、无询问、无额外日志节点
# ---------------------------------------------------------------------------


def test_without_a_rejudge_skill_the_window_stays_empty_and_silent():
    """无改判能力者 ⇒ 队列恒空、无询问，判定三时点序不变（票 11 §验收 4 / 7）。"""
    original = _card_id(black=True)
    state = _judgment_state(
        seats=[SeatSpec(hp=4, judge=(INDULGENCE,)), SeatSpec(hp=4)],
        deck=_deck(original),
    )

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(), Scripted()], turns=1)

    assert _respond_queries(engine, WindowCause.REJUDGE) == [], "队列恒空 ⇒ 不构造询问"
    judge_timings = [
        timing for timing in _timings(_first_turn(engine)) if timing.value.startswith("JUDGE_")
    ]
    assert judge_timings == [
        Timing.JUDGE_ON,
        Timing.JUDGE_CARD_BEFORE_EFFECT,
        Timing.JUDGE_CARD_AFTER_EFFECT,
    ], "判定帧的整体时点序不变"


def test_the_judge_timing_order_is_unchanged_even_when_someone_rejudges():
    """有人改判也不插新时点：仍是 ON → BEFORE → AFTER（票 11 §验收 7）。"""
    original = _card_id(black=True)
    replacement = _card_id(black=False)
    state = _judgment_state(
        seats=[
            SeatSpec(hp=4, judge=(INDULGENCE,)),
            SeatSpec(hp=4, hero="simayi", hand=(replacement,)),
        ],
        deck=_deck(original, replacement),
    )

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(), Scripted(lambda q: _play(q, replacement))], turns=1)

    judge_timings = [
        timing for timing in _timings(_first_turn(engine)) if timing.value.startswith("JUDGE_")
    ]
    assert judge_timings == [
        Timing.JUDGE_ON,
        Timing.JUDGE_CARD_BEFORE_EFFECT,
        Timing.JUDGE_CARD_AFTER_EFFECT,
    ]


# ---------------------------------------------------------------------------
# ⑤ 窗口绑帧：窗口内有人死亡 ⇒ 应答者区间重滤
# ---------------------------------------------------------------------------


def test_a_seat_that_dies_inside_the_window_is_not_asked():
    """座位 0 应答时座位 1 死亡 ⇒ 重开时重滤应答者，死者不再被问（票 11 §验收 5）。"""
    original, _first, _second, state = _two_rejudgers()
    holder: dict[str, Engine] = {}

    def killer(query):
        if query.kind == "RESPOND" and query.cause is WindowCause.REJUDGE:
            holder["engine"].state.player(1).alive = False
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = Engine.from_state(state, seed=1)
    holder["engine"] = engine
    run_turns(engine, [Scripted(killer), Scripted()], turns=1)

    window = _respond_queries(engine, WindowCause.REJUDGE)
    assert [query.actor for query in window] == [0], "死者按存活重滤 ⇒ 不再被问"
    assert original in engine.state.discard, "无人替代 ⇒ 原判定牌照常进弃牌堆"


# ---------------------------------------------------------------------------
# ⑥ 天妒：拿到最终生效的那张判定牌
# ---------------------------------------------------------------------------


def test_tiandu_takes_the_judge_card_and_keeps_it_out_of_the_discard():
    """郭嘉【天妒】获得此判定牌（票 11 §验收 6）。"""
    judge_card = _card_id(black=True)
    state = _judgment_state(
        seats=[SeatSpec(hp=3, hp_max=3, hero="guojia", judge=(INDULGENCE,)), SeatSpec(hp=4)],
        deck=_deck(judge_card),
    )

    def taker(query):
        return _activate(query, TIANDU)

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(taker), Scripted()], turns=1)

    assert judge_card in engine.state.player(0).hand, "生效后获得此判定牌"
    assert judge_card not in engine.state.discard, "已被获得 ⇒ 不再入弃牌堆"


def test_tiandu_is_not_offered_when_the_judgment_is_someone_elses():
    """【天妒】只在**自己的**判定牌生效后触发（正面对照）。"""
    judge_card = _card_id(black=True)
    state = _judgment_state(
        seats=[SeatSpec(hp=4, judge=(INDULGENCE,)), SeatSpec(hp=3, hp_max=3, hero="guojia")],
        deck=_deck(judge_card),
    )

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(), Scripted()], turns=1)

    assert all(query.skill_id != TIANDU for query in _queries(engine, "SKILL_ACTIVATION"))
    assert judge_card in engine.state.discard


def test_tiandu_takes_the_final_card_after_a_rejudge_and_not_the_discarded_original():
    """被改判掉的原牌不触发天妒；天妒拿到的是最终生效的那张（票 11 §验收 6）。"""
    original = _card_id(black=True)
    replacement = _card_id(black=False)
    state = _judgment_state(
        seats=[
            SeatSpec(hp=3, hp_max=3, hero="guojia", judge=(INDULGENCE,)),
            SeatSpec(hp=4, hero="simayi", hand=(replacement,)),
        ],
        deck=_deck(original, replacement),
    )

    def rejudger(query):
        return _play(query, replacement)

    def taker(query):
        return _activate(query, TIANDU)

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(taker), Scripted(rejudger)], turns=1)

    assert _respond_queries(engine, WindowCause.REJUDGE), "先改判"
    assert replacement in engine.state.player(0).hand, "天妒拿最终生效的那张"
    assert replacement not in engine.state.discard
    assert original in engine.state.discard, "被替代掉的原牌照旧进弃牌堆"


# ---------------------------------------------------------------------------
# ⑦ 改判「代替」的可重入（code-review C2）
# ---------------------------------------------------------------------------


def test_a_rejudge_that_suspends_mid_substitution_still_discards_the_original_card():
    """替代的第一段移动挂起后，重入必须把**原判定牌**送进弃牌堆（code-review C2）。

    改判者同时持有【鬼才】（虚拟授予）与【连营】：替代用的最后一张手牌离开手牌时
    【连营】提问 ⇒ ``substitute_judge_card`` 的第一段移动挂起。旧实现在重入时把已经
    写进 ``judge_card`` 的新牌当成原判定牌，真正的原牌因此**滞留处理区**——本用例钉住
    "原牌进弃牌堆、处理区不留牌"。
    """
    original = _card_id(black=True)
    replacement = _card_id(black=False)
    state = _judgment_state(
        seats=[
            SeatSpec(hp=4, judge=(INDULGENCE,)),
            SeatSpec(
                hp=4,
                hero="luxun",
                hand=(replacement,),
                virtual_equipment=(GUICAI,),
            ),
        ],
        deck=_deck(original, replacement),
    )

    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [Scripted(), Scripted(lambda query: _play(query, replacement))], turns=1)

    assert [query.actor for query in _respond_queries(engine, WindowCause.REJUDGE)] == [1], (
        "虚拟授予的【鬼才】让座位 1 进改判窗口"
    )
    assert any(
        query.kind == "SKILL_ACTIVATION" and query.skill_id == LIANYING
        for query in _queries(engine, "SKILL_ACTIVATION")
    ), "替代的手牌离手 ⇒ 【连营】真的提了问（挂起路径被走到）"
    assert original in engine.state.discard, "原判定牌必须进弃牌堆"
    assert original not in engine.state.processing, "原判定牌不得滞留处理区"
    assert replacement in engine.state.discard, "结算结束后新判定牌进弃牌堆"

