"""票 28：无懈可击与响应链（库门面主缝）。

断言全部落在**记录流与棋盘状态**上：时机 / delta / query / action、体力、区域、手牌。
不碰帧私有 ``data`` / ``pc``。仅有的例外是两条**牌面事实**断言（响应窗口的声明落在行为表），
与 ``tests/test_judgment.py`` 的 ``passes_to_next_judge`` 同姿态：它们是"牌怎么写的"，
不是"引擎怎么实现的"。

被测的票面行为（`issues/28-nullify-and-response-chain.md`，决策回
`issues/02-…` / `issues/11-…` / `issues/18-…`）：

- 无懈是**使用**不是打出（走 USE 帧、不占使用次数）；被抵消的原锦囊**仍算已使用**；
- 无懈的目标是"这次结算 + 目标角色"二元组：窗口开在结算的"生效前"、**逐目标**才轮到；
- 嵌套用**嵌套结算帧**表达、**出栈即传播**（奇偶律退化为链长奇偶）：深一层总先出栈，
  成功则把"它所指向的那层"标记为被抵消，被抵消的那层什么都不抵消；
- 收齐式窗口：全体存活**每人每层一次**（按优先级降序），收齐后**只有优先级最高者**
  实际使用，其余声明不耗牌、无使用事件；
- 响应窗口按行为表的 ``response_window`` 声明分派（【杀】= 闪窗口、锦囊 = 无懈窗口）；
- 延时锦囊的无懈窗口取**约定 B**：在目标角色判定阶段的那张结算帧上；
  闪电被无懈 ⇒ 移交下家判定区，乐不思蜀被无懈 ⇒ 进弃牌堆。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sgs.data import RuleConfig, load_content_pack
from sgs.effects import CARD_BEHAVIORS, FinalZone, ResponseWindow, legal_actions_for
from sgs.engine import Engine, dump_journal, read_journal
from sgs.engine.steps import _collected_response_window, _dodge_window
from sgs.resolution import Frame
from sgs.state import (
    ActivateSkill,
    AskPolicy,
    DodgeContext,
    EndPhase,
    FrameKind,
    MoveReason,
    Pass,
    PeachContext,
    Phase,
    PlayCard,
    QueryKind,
    RespondContext,
    RespondMode,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support.positions import SeatSpec, build_state, cards_of_type, run_turns, run_until

STANDARD = "standard_108"

RESOLVE_STAGES_CANCELED = (
    Timing.RESOLVE_START,
    Timing.RESOLVE_BEFORE_EFFECT,
    Timing.RESOLVE_END,
)
"""被抵消的结算：C1 与 C2 照发、直达 C5（**不出现**生效时 / 生效后，票 11 §6）。"""

RESOLVE_STAGES_FULL = (
    Timing.RESOLVE_START,
    Timing.RESOLVE_BEFORE_EFFECT,
    Timing.RESOLVE_EFFECT,
    Timing.RESOLVE_AFTER_EFFECT,
    Timing.RESOLVE_END,
)

NULLIFY_TRICKS = (
    "dismantlement",
    "snatch",
    "ex_nihilo",
    "duel",
    "borrowed_sword",
    "barbarian_invasion",
    "arrow_barrage",
    "peach_garden",
    "harvest",
    "nullify",
    "indulgence",
    "lightning",
)


@pytest.fixture(scope="module")
def pack():
    return load_content_pack(deck_profile=STANDARD)


# ---------------------------------------------------------------------------
# 驱动：一个座位一个应答器
# ---------------------------------------------------------------------------


@dataclass
class _Plan:
    play: str = ""
    """出牌阶段要使用的牌名（命中后转结束阶段）。"""

    nullify: dict[int, int] = field(default_factory=dict)
    """座位 → 允许在无懈窗口里**声明**的次数（用来制造"声明了但不生效"的输家）。"""

    respond: str = "pass"
    """非无懈的 RESPOND 窗口：``play`` = 打出第一张候选、其余放弃。"""

    played: bool = False


class _Planned:
    def __init__(self, plan: _Plan) -> None:
        self.plan = plan

    def respond(self, query):
        plan = self.plan
        if query.kind == QueryKind.PLAY_PHASE:
            if plan.play and not plan.played:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == plan.play:
                        plan.played = True
                        return action
            return _find(query, EndPhase)
        if query.kind == QueryKind.RESPOND:
            if query.cause is WindowCause.NULLIFY:
                if plan.nullify.get(query.actor, 0) > 0:
                    card = _first(query, UseCard)
                    if card is not None:
                        plan.nullify[query.actor] -= 1
                        return card
                return _find(query, Pass)
            if plan.respond == "play":
                card = _first(query, PlayCard)
                if card is not None:
                    return card
            return _find(query, Pass)
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
    excluded = set(card_ids)
    return tuple(index for index in range(len(pack.deck.cards)) if index not in excluded)


def _engine(
    pack,
    *,
    play: str = "",
    seats: list[SeatSpec],
    deck: tuple[int, ...] | None = None,
    turn_seat: int = 0,
    seed: int = 1,
    nullify: dict[int, int] | None = None,
    respond: str = "pass",
    phase=None,
    ask_policy: AskPolicy | None = None,
):
    plan = _Plan(play=play, nullify=dict(nullify or {}), respond=respond)
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=deck if deck is not None else _deck_excluding(pack, *used),
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(first_seat=turn_seat),
    )
    # ``ask_policy=None`` = 引擎缺省（OFF）：本文件绝大多数用例断的是**旧口径**。
    # 票 05 的用例显式传 ``AskPolicy(auto_skip=True)`` 构造网页会话的口径。
    engine = Engine.from_state(
        state, seed=seed, ask_policy=ask_policy if ask_policy is not None else AskPolicy()
    )
    players = [_Planned(plan) for _ in seats]
    return engine, plan, players


def _run_trick(pack, **kwargs):
    engine, plan, players = _engine(pack, **kwargs)
    play = plan.play
    run_until(engine, players, lambda e: plan.played and _use_settled(e, play))
    return engine, plan


def _run_turn(pack, **kwargs):
    engine, plan, players = _engine(pack, **kwargs)
    run_turns(engine, players, turns=1)
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


def _deltas(engine: Engine, delta_type: str) -> list:
    return [r for r in engine.records if r.kind == "delta" and r.delta_type == delta_type]


def _resolve_frame_id(engine: Engine) -> int:
    """记录流里的**第一个** RESOLVE 帧 = 原锦囊那次使用的结算帧。"""
    return next(
        record.frame_id
        for record in engine.records
        if record.kind == "frame" and record.frame_kind.value == "RESOLVE"
    )


def _stages(engine: Engine, frame_id: int) -> list[Timing]:
    return [
        record.timing
        for record in engine.records
        if record.kind == "timing"
        and record.frame_id == frame_id
        and record.timing.value.startswith("RESOLVE_")
    ]


def _use_frames(engine: Engine) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind.value == "USE"
    ]


def _declared(engine: Engine, card_id: int) -> list:
    return [
        record.action
        for record in engine.records
        if record.kind == "action"
        and record.accepted
        and record.action.kind == "UseCard"
        and record.action.card.card_id == card_id
    ]


# ---------------------------------------------------------------------------
# 响应窗口的声明面
# ---------------------------------------------------------------------------


def test_the_response_window_is_declared_on_the_behavior_table():
    assert CARD_BEHAVIORS["slash"].response_window is ResponseWindow.DODGE
    for type_id in NULLIFY_TRICKS:
        assert CARD_BEHAVIORS[type_id].response_window is ResponseWindow.NULLIFY, type_id
    assert CARD_BEHAVIORS["peach"].response_window is None
    assert CARD_BEHAVIORS["dodge"].response_window is None
    for behavior in CARD_BEHAVIORS.values():
        if behavior.final_zone is FinalZone.EQUIP:
            assert behavior.response_window is None


def test_nullify_is_declared_as_an_unlimited_response_use():
    behavior = CARD_BEHAVIORS["nullify"]

    assert behavior.use_limit.rated is None, "无懈不占使用次数"
    assert behavior.target_rule.rated_targets == 0, "目标由窗口帧承载，行动只带牌"
    assert behavior.effects == (), "抵消由出栈传播承担，不是效果步"


# ---------------------------------------------------------------------------
# 单体锦囊：被无懈抵消
# ---------------------------------------------------------------------------


def test_a_nullify_use_cancels_the_trick_and_leaves_it_used(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[SeatSpec(hand=(ex_nihilo,)), SeatSpec(hand=(nullify,)), SeatSpec()],
        nullify={1: 1},
    )
    frame_id = _resolve_frame_id(engine)

    assert _stages(engine, frame_id) == list(RESOLVE_STAGES_CANCELED)
    assert engine.state.player(0).card_use_counts.get("ex_nihilo") == 1, "被抵消仍算已使用"
    assert ex_nihilo in engine.state.discard
    assert nullify in engine.state.discard
    assert not [
        d
        for d in _deltas(engine, "card_move")
        if d.reason is MoveReason.DRAW and d.frame_id == frame_id
    ], "被抵消 ⇒ 不摸牌（摸牌阶段的摸牌不属于这次结算）"

    window = _queries(engine, QueryKind.RESPOND)[0]
    assert window.cause is WindowCause.NULLIFY
    assert window.required == "nullify"
    assert window.target == 0, "目标是'这张锦囊对座位 0 的这次结算'"
    assert window.window_frame == frame_id


def test_nullify_resolves_as_a_use_frame_not_a_play(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[SeatSpec(hand=(ex_nihilo,)), SeatSpec(hand=(nullify,)), SeatSpec()],
        nullify={1: 1},
    )

    assert [record.frame_kind.value for record in _use_frames(engine)] == ["USE", "USE"], (
        "原锦囊 + 无懈各一次使用"
    )
    assert not _timings(engine, Timing.PLAY_DECLARE), "无懈是使用，不走 L6"
    assert _timings(engine, Timing.USE_ON_USE), "走 L4 的'使用时'"


# ---------------------------------------------------------------------------
# 嵌套：出栈即传播、链长奇偶
# ---------------------------------------------------------------------------


def test_a_nullify_of_a_nullify_restores_the_original_trick(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    first = cards_of_type(pack, "nullify")[0]
    second = cards_of_type(pack, "nullify")[1]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[
            SeatSpec(hand=(ex_nihilo,)),
            SeatSpec(hand=(first,)),
            SeatSpec(hand=(second,)),
        ],
        nullify={1: 1, 2: 2},
    )
    frame_id = _resolve_frame_id(engine)

    assert _stages(engine, frame_id) == list(RESOLVE_STAGES_FULL), "链长 2 ⇒ 原锦囊不被抵消"
    assert first in engine.state.discard and second in engine.state.discard
    draws = [
        d
        for d in _deltas(engine, "card_move")
        if d.reason is MoveReason.DRAW
        and d.to_zone.zone is Zone.HAND
        and d.frame_id == frame_id
    ]
    assert [len(d.cards) for d in draws] == [2]

    windows = _queries(engine, QueryKind.RESPOND)
    assert {w.cause for w in windows} == {WindowCause.NULLIFY}
    assert sorted({w.target for w in windows}) == [0, 1, 2], (
        "每层窗口针对它要抵消的那次结算：原锦囊对座位 0、两层无懈各对它的使用者"
    )
    assert len({w.window_frame for w in windows}) == 3, "每层一个挂起帧"


def test_three_layers_of_nullify_end_with_the_original_trick_cancelled(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    cards = cards_of_type(pack, "nullify")[:3]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[
            SeatSpec(hand=(ex_nihilo,)),
            SeatSpec(hand=(cards[0],)),
            SeatSpec(hand=(cards[1],)),
            SeatSpec(hand=(cards[2],)),
        ],
        nullify={1: 1, 2: 2, 3: 3},
    )
    frame_id = _resolve_frame_id(engine)

    assert _stages(engine, frame_id) == list(RESOLVE_STAGES_CANCELED), "链长 3 ⇒ 原锦囊被抵消"
    assert all(card in engine.state.discard for card in cards)
    assert not [
        d
        for d in _deltas(engine, "card_move")
        if d.reason is MoveReason.DRAW and d.frame_id == frame_id
    ]


def test_the_same_seat_may_use_several_nullify_cards_in_one_turn(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    first = cards_of_type(pack, "nullify")[0]
    second = cards_of_type(pack, "nullify")[1]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[SeatSpec(hand=(ex_nihilo,)), SeatSpec(hand=(first, second)), SeatSpec()],
        nullify={1: 2},
    )
    frame_id = _resolve_frame_id(engine)

    assert _stages(engine, frame_id) == list(RESOLVE_STAGES_FULL)
    assert first in engine.state.discard and second in engine.state.discard
    assert engine.state.player(1).card_use_counts.get("nullify") == 2, "计数照记、不受额定上限"


# ---------------------------------------------------------------------------
# 收齐式窗口：优先级最高者生效、其余不打出
# ---------------------------------------------------------------------------


def test_only_the_highest_priority_declaration_is_actually_used(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    winner = cards_of_type(pack, "nullify")[0]
    loser = cards_of_type(pack, "nullify")[1]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[
            SeatSpec(hand=(ex_nihilo,)),
            SeatSpec(hand=(winner,)),
            SeatSpec(hand=(loser,)),
            SeatSpec(),
        ],
        nullify={1: 1, 2: 1},
    )
    frame_id = _resolve_frame_id(engine)

    assert _stages(engine, frame_id) == list(RESOLVE_STAGES_CANCELED)
    assert winner in engine.state.discard
    assert loser in engine.state.player(2).hand, "声明了但没生效的那张不耗牌"
    assert loser not in engine.state.discard
    assert len(_use_frames(engine)) == 2, "只多出**一张**无懈的使用事件"
    assert len(_declared(engine, loser)) == 1, "座位 2 的声明被接受，但没有产生使用"


def test_every_alive_seat_is_asked_once_per_layer_even_without_nullify(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[SeatSpec(hand=(ex_nihilo,)), SeatSpec(hand=(nullify,)), SeatSpec()],
        nullify={1: 1},
    )
    layer_one = [
        q
        for q in _queries(engine, QueryKind.RESPOND)
        if q.cause is WindowCause.NULLIFY and q.target == 0
    ]

    assert [q.actor for q in layer_one] == [0, 1, 2], "从当前回合角色起逆时针、每人一次"
    assert all(_first(q, UseCard) is None for q in layer_one[:1]), (
        "没有无懈的座位仍会被问，候选里只有放弃"
    )
    assert all(any(isinstance(a, Pass) for a in q.legal_actions) for q in layer_one)


# ---------------------------------------------------------------------------
# 逐目标窗口：一个目标被抵消不影响另一个
# ---------------------------------------------------------------------------


def test_the_nullify_window_opens_per_target_of_a_multi_target_trick(pack):
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    engine, _ = _run_trick(
        pack,
        play="barbarian_invasion",
        seats=[
            SeatSpec(hand=(barbarian,)),
            SeatSpec(hand=(nullify,)),
            SeatSpec(),
            SeatSpec(),
        ],
        nullify={1: 1},
        respond="pass",
    )
    frame_id = _resolve_frame_id(engine)

    after_effect = [
        record.targets[0]
        for record in _timings(engine, Timing.RESOLVE_AFTER_EFFECT)
        if record.frame_id == frame_id
    ]
    assert after_effect == [2, 3], "座位 1 的那次结算被抵消，2 / 3 照常结算"
    assert [player.hp for player in engine.state.players] == [4, 4, 3, 3]
    assert engine.state.player(0).card_use_counts.get("barbarian_invasion") == 1


# ---------------------------------------------------------------------------
# 延时锦囊：窗口在判定阶段（约定 B），被无懈后的去向
# ---------------------------------------------------------------------------


def test_a_nullified_lightning_moves_to_the_next_judge_zone_without_a_judgment(pack):
    lightning = cards_of_type(pack, "lightning")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    engine, _ = _run_turn(
        pack,
        seats=[SeatSpec(judge=(lightning,)), SeatSpec(hand=(nullify,))],
        deck=_deck_excluding(pack, lightning, nullify),
        phase=Phase.PREPARE,
        nullify={1: 1},
    )

    assert not [t for t in _timings(engine, Timing.JUDGE_ON)]
    assert not [t for t in _timings(engine, Timing.JUDGE_CARD_AFTER_EFFECT)]
    passes = [
        record
        for record in _deltas(engine, "card_move")
        if record.reason is MoveReason.PUT
        and record.to_zone.zone is Zone.JUDGE
        and record.cards[0].card_id == lightning
    ]
    assert [(record.to_zone.seat, record.owner) for record in passes] == [(1, None)]
    assert nullify in engine.state.discard


def test_a_nullified_indulgence_goes_to_discard_and_does_not_skip_the_play_phase(pack):
    indulgence = cards_of_type(pack, "indulgence")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    engine, _ = _run_turn(
        pack,
        seats=[SeatSpec(judge=(indulgence,)), SeatSpec(hand=(nullify,))],
        deck=_deck_excluding(pack, indulgence, nullify),
        phase=Phase.PREPARE,
        nullify={1: 1},
    )

    assert indulgence in engine.state.discard
    assert not [t for t in _timings(engine, Timing.JUDGE_ON)]
    assert Timing.PHASE_PLAY_START in [r.timing for r in engine.records if r.kind == "timing"], (
        "被抵消 ⇒ 不跳过出牌阶段"
    )


# ---------------------------------------------------------------------------
# 确定性与记录面往返
# ---------------------------------------------------------------------------


def test_the_nullify_chain_is_reproducible_and_round_trips(pack, tmp_path):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    first = cards_of_type(pack, "nullify")[0]
    second = cards_of_type(pack, "nullify")[1]

    def run() -> str:
        engine, _ = _run_trick(
            pack,
            play="ex_nihilo",
            seats=[
                SeatSpec(hand=(ex_nihilo,)),
                SeatSpec(hand=(first,)),
                SeatSpec(hand=(second,)),
            ],
            nullify={1: 1, 2: 2},
            seed=17,
        )
        return engine.journal()

    text = run()
    assert text == run(), "嵌套无懈不引入随机来源：同种子逐字节复现"

    path = tmp_path / "nullify.jsonl"
    path.write_text(text, encoding="utf-8")
    restored = read_journal(path)

    assert dump_journal(restored) == text
    window = next(
        record.query
        for record in restored
        if record.kind == "query" and record.query.kind == QueryKind.RESPOND
    )
    assert window.cause is WindowCause.NULLIFY
    assert window.required == "nullify"


# ---------------------------------------------------------------------------
# B1：无懈只响应锦囊——六类 ``required`` 响应窗口按**牌名**收口
# ---------------------------------------------------------------------------
#
# 根因两条（`.scratch/w5-borrowed-sword/issues/01`）：`_dodge` 用 `_usable_hand` 只按
# "语境 + 时机"取牌、不按牌名，而【无懈可击】的 `use_timings` 恰好命中 ⇒ 它进【闪】候选；
# `_dodge_window` 又把任何 `UseCard` 都算一次【闪】。修法在**共用候选生成**上按 ``required``
# 牌名收口（不是按 ``QueryKind`` 一刀切），拒绝【八卦阵】/【国色】这类合法路径被误修。


def _response_state(pack):
    """双人局面：座位 1 手里同时有【杀】【闪】【无懈可击】——六类窗口都从这里取候选。"""
    slash = cards_of_type(pack, "slash")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    return build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(hand=(slash, dodge, nullify))],
        deck=_deck_excluding(pack, slash, dodge, nullify),
        turn_seat=0,
    )


_REQUIRED_RESPONSE_WINDOWS = (
    (
        "DODGE（闪）",
        "dodge",
        lambda state: legal_actions_for(
            state, 1, QueryKind.DODGE, answers_seq=1, payload=DodgeContext()
        ),
    ),
    (
        "BARBARIAN（打出杀）",
        "slash",
        lambda state: legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.BARBARIAN_INVASION, required="slash"
            ),
        ),
    ),
    (
        "ARROW（打出闪）",
        "dodge",
        lambda state: legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(cause=WindowCause.ARROW_BARRAGE, required="dodge"),
        ),
    ),
    (
        "DUEL（打出杀）",
        "slash",
        lambda state: legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(cause=WindowCause.DUEL, required="slash"),
        ),
    ),
    (
        "BORROWED_SWORD（使用杀）",
        "slash",
        lambda state: legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.BORROWED_SWORD,
                required="slash",
                target=1,
                mode=RespondMode.USE,
                use_targets=(0,),
            ),
        ),
    ),
    (
        "NULLIFY（无懈）",
        "nullify",
        lambda state: legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.NULLIFY,
                required="nullify",
                target=1,
                mode=RespondMode.USE,
            ),
        ),
    ),
)
"""六类带 ``required`` 的响应窗口：DODGE 走独立 kind，其余五类走 ``RESPOND``。

两列的对应关系就是被测的契约：**候选牌的类型 == required 牌名**。【无懈可击】的
``use_timings`` 命中"响应 + 生效前"，所以只在 DODGE 这一行里它是**非法**候选。"""


@pytest.mark.parametrize(
    ("label", "required", "window"),
    _REQUIRED_RESPONSE_WINDOWS,
    ids=[row[0] for row in _REQUIRED_RESPONSE_WINDOWS],
)
def test_every_required_response_window_offers_only_cards_of_that_name(
    pack, label, required, window
):
    """表驱动：漏哪个窗口就红哪个参数——先测出真漏，再一处收口。"""
    actions = window(_response_state(pack))

    cards = [
        action.card
        for action in actions
        if isinstance(action, (UseCard, PlayCard))
    ]
    assert cards, f"{label}：窗口没有真牌候选，表驱动没有测到东西"
    assert {card.type for card in cards} == {required}, (
        f"{label}：响应窗口只收 required={required!r} 的牌；"
        f"实收 {sorted({card.type for card in cards})!r}"
    )


def test_the_dodge_window_never_offers_a_nullify(pack):
    """【无懈可击】只在锦囊的结算里可用：手里有它也不能进【闪】候选。"""
    slash = cards_of_type(pack, "slash")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(dodge, nullify))],
        deck=_deck_excluding(pack, slash, dodge, nullify),
        turn_seat=0,
    )

    actions = legal_actions_for(
        state, 1, QueryKind.DODGE, answers_seq=1, payload=DodgeContext()
    )

    offered = [action.card.card_id for action in actions if isinstance(action, UseCard)]
    assert offered == [dodge], "【闪】候选恰是那张真【闪】，【无懈可击】不在其中"


class _DodgeWindowHost:
    """``_dodge_window`` 的最小宿主：把剧本里那一条应答交回去，不压帧、不派发。

    "无懈不能当【闪】"要测的是**抵消判定**本身（候选收口之后也不 fail-open）：
    窗口拿到一张非【闪】的 ``UseCard`` 时不许把它记成响应，更不许替它压出使用帧。
    """

    def __init__(self, state, answer) -> None:
        self.state = state
        self.answer = answer
        self.used = []

    def advance_provider(self, frame, timing):
        return None, None, None

    def should_ask_query(self, kind, actor, **kwargs):
        # 票 05 的预筛口：本宿主只钉"非【闪】不许当成响应"，一律照问（引擎缺省 OFF）。
        return True

    def ask_query(self, kind, actor, timing, *, frame=None, payload=None):
        return self.answer

    def suspended(self, action):
        return False

    def provider_registered(self, skill_id):
        raise AssertionError("本用例不提供提供者")

    def start_provider(self, frame, skill_id, actor, timing, *, required):
        raise AssertionError("本用例不提供提供者")

    def open_skill_judgment(self, frame, seat, skill_id):
        raise AssertionError("本用例不提供提供者")

    def start_use(self, card, **kwargs):
        self.used.append(card)


def test_a_nullify_cannot_be_the_dodge_that_cancels_a_slash(pack):
    """即使窗口收到一张【无懈可击】，它也不构成响应：【杀】照常结算。"""
    slash = cards_of_type(pack, "slash")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(nullify,))],
        deck=_deck_excluding(pack, slash, nullify),
        turn_seat=0,
    )
    frame = Frame(
        kind=FrameKind.RESOLVE,
        data={"source": 0, "card": state.card(slash), "targets": (1,)},
    )
    host = _DodgeWindowHost(
        state, UseCard(card=state.card(nullify), targets=(), answers_seq=1)
    )

    control = _dodge_window(host, frame, 1)

    assert control == "stay"
    assert frame.data.get("dodges_done", 0) == 0, "【无懈可击】不算一次【闪】"
    assert frame.data["canceled"] is False, "【无懈可击】不能抵消【杀】"
    assert host.used == [], "【无懈可击】不能被当成【闪】压进使用链"


class _NullifyWindowHost:
    """``_collected_response_window`` 的最小宿主：窗口那一问交回一条 ``ActivateSkill``。

    票 02 的 fail-closed 反例：应答归属修好之后，窗口仍要拒绝"没有惰性判定式提供者"的
    ``ActivateSkill``——**不得**静默当成 Pass。提供者路径全部设成"不该被调用"。
    """

    def __init__(self, state, answer) -> None:
        self.state = state
        self.answer = answer

    def advance_provider(self, frame, timing):
        return None, None, None

    def should_ask_respond(self, actor, **kwargs):
        # 票 05 的预筛口：本宿主只钉"未登记提供者 fail-closed"，一律照问
        # （与引擎缺省 OFF 同一口径；预筛选人与否由真引擎的用例覆盖）。
        return True

    def ask_respond(self, frame, actor, timing, **kwargs):
        return self.answer

    def suspended(self, action):
        return False

    def provider_registered(self, skill_id):
        return False

    def start_provider(self, frame, skill_id, actor, timing, *, required):
        raise AssertionError("未登记的 ActivateSkill 不许走提供者派发")


def test_a_nullify_window_still_rejects_an_activation_without_a_provider(pack):
    """真的没有提供者回调的 ``ActivateSkill`` 仍 fail-closed（不静默当 Pass）。"""
    state = _response_state(pack)
    frame = Frame(
        kind=FrameKind.RESOLVE,
        data={
            "source": 0,
            "card": state.card(cards_of_type(pack, "duel")[0]),
            "targets": (1,),
        },
        frame_id=7,
    )
    host = _NullifyWindowHost(
        state, ActivateSkill(skill_id="hero.jie_simayi.fankui", answers_seq=1)
    )

    with pytest.raises(NotImplementedError, match="无懈窗口没有判定式提供者"):
        _collected_response_window(host, frame, 1)

    assert frame.data.get("response_declared") == [], "被拒绝的声明不许进声明表"


# ---------------------------------------------------------------------------
# 询问策略位（table-ux-overhaul 票 05 / spec §2 Q3）：无懈窗口只问合格者
#
# 五处 A 类窗口在发问**之前**先过票 02 的统一预筛口（``should_ask_respond`` /
# ``should_ask_query``），窗口类别位由窗口自己声明。**引擎缺省 OFF** ⇒ 旧口径逐字节不变
# （本文件上面那条 ``test_every_alive_seat_is_asked_once_per_layer_even_without_nullify``
# 就是 OFF 的判据，一条不改）；下面几条全部在**策略 ON** 的引擎上构造。
#
# 「合格」= **同一个候选生成器**能给出的非 ``Pass`` 候选（真牌 ∪ 转化 / 提供者），不是
# 另写一份"有没有真无懈"的判据——票 02 的 ``respond_candidates`` 就是窗口真发问时的那份。
# ---------------------------------------------------------------------------


def _nullify_layer(engine: Engine, target: int = 0) -> list:
    """某一层无懈窗口的询问（``target`` = 这一次结算针对的座位）。"""
    return [
        query
        for query in _queries(engine, QueryKind.RESPOND)
        if query.cause is WindowCause.NULLIFY and query.target == target
    ]


def _black_card(pack, *exclude: int) -> int:
    """一张**黑色**牌（【看破】的源牌谓词是"黑色手牌"）。"""
    from sgs.data import Color, color_of_suit

    for card_id, card in enumerate(pack.deck.cards):
        if card_id in exclude:
            continue
        if color_of_suit(card.suit) is Color.BLACK:
            return card_id
    raise AssertionError("标准 108 档里没有黑色牌？")


def test_a_seat_without_a_nullify_or_any_conversion_is_not_asked(pack):
    """ON：真【无懈可击】0、任何转化 / 提供者 0 的座位**连询问都不发**（不再只有 Pass）。"""
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[SeatSpec(hand=(ex_nihilo,)), SeatSpec(hand=(nullify,)), SeatSpec()],
        nullify={1: 1},
        ask_policy=AskPolicy(auto_skip=True),
    )

    assert [query.actor for query in _nullify_layer(engine)] == [1], (
        "只有持真【无懈可击】的座位 1 被问；座位 0（使用者）与座位 2（空手）不该收到"
        "只有 Pass 的询问"
    )
    assert _stages(engine, _resolve_frame_id(engine)) == list(RESOLVE_STAGES_CANCELED), (
        "跳过空座位不改变声明语义：座位 1 的声明照样抵消这张锦囊"
    )


def test_a_seat_with_kanpo_black_card_is_still_asked(pack):
    """卧龙诸葛【看破】：真【无懈可击】0、黑牌 1 ⇒ ON 也必须被问（转化能力算合格）。"""
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    black = _black_card(pack, ex_nihilo)
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[
            SeatSpec(hand=(ex_nihilo,)),
            SeatSpec(hero="wolong_zhugeliang", hand=(black,)),
            SeatSpec(),
        ],
        nullify={},
        ask_policy=AskPolicy(auto_skip=True),
    )

    layer = _nullify_layer(engine)
    assert [query.actor for query in layer] == [1], (
        "手里没有真【无懈可击】但有【看破】的座位仍必须被问"
    )
    # 【看破】是**单源转化**：候选是一张虚拟【无懈可击】的 `UseCard`（不是 `ActivateSkill`），
    # 住在询问的**转化清单**里（`transform_actions`——W4-fix-2 把它从 `legal_actions` 收回），
    # 源牌就是那张黑牌。它在这张面上 ⇒ 预筛读的是与真询问**同一份**生成器。
    virtuals = [action.card for action in layer[0].transform_actions]
    assert [(card.type, tuple(card.virtual_sources)) for card in virtuals] == [
        ("nullify", (black,))
    ], f"【看破】的虚拟无懈就在这张面里。实得 {virtuals}"


def test_a_seat_with_jianying_conversion_is_still_asked(pack):
    """界【渐营】②（求桃窗口的提供者）：真【桃】0、转化能力 1 ⇒ ON 也必须被问。

    这一扇走 ``ask_query``（``QueryKind.PEACH``），预筛口是它的姊妹
    ``should_ask_query``：预览与真询问读**同一份** ``PeachContext``（``floor`` 同源）。
    """
    slash = cards_of_type(pack, "slash")[0]
    source = _black_card(pack, slash)
    engine, plan, players = _engine(
        pack,
        play="slash",
        seats=[
            SeatSpec(hero="jie_jushou_mobile", hp=3, hp_max=3, hand=(slash, source)),
            SeatSpec(hp=1),
        ],
        turn_seat=0,
        phase=Phase.PLAY,
        ask_policy=AskPolicy(auto_skip=True),
    )
    assert plan.play == "slash"
    run_until(engine, players, lambda e: bool(_queries(e, QueryKind.PEACH)))

    peaches = _queries(engine, QueryKind.PEACH)
    assert peaches, "座位 1 被打到 0 体力，求桃窗口应当打开"
    assert peaches[0].actor == 0, "回合角色先被问"
    assert peaches[0].dying == 1
    offers = [
        action.skill_id
        for action in peaches[0].legal_actions
        if isinstance(action, ActivateSkill)
    ]
    assert [skill_id for skill_id in offers if skill_id.endswith(".jianying")], (
        f"没有真【桃】但有界【渐营】② 的座位仍必须被问。实得 {offers}"
    )


def test_two_declarations_keep_the_higher_priority_one_and_the_loser_keeps_the_card(pack):
    """ON：两条声明 ⇒ 首个声明者（应答者区间序）生效，输家的牌不消耗、不压帧。

    策略只改"问不问"，**不改**收集式窗口的赢家 / 输家语义（spec 票 05 §要做什么 3）。
    """
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    winner = cards_of_type(pack, "nullify")[0]
    loser = cards_of_type(pack, "nullify")[1]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[
            SeatSpec(hand=(ex_nihilo,)),
            SeatSpec(hand=(winner,)),
            SeatSpec(hand=(loser,)),
            SeatSpec(),
        ],
        nullify={1: 1, 2: 1},
        ask_policy=AskPolicy(auto_skip=True),
    )

    assert [query.actor for query in _nullify_layer(engine)] == [1, 2], (
        "两个持牌的座位都被问；空手座位不在这条链里"
    )
    frame_id = _resolve_frame_id(engine)
    assert _stages(engine, frame_id) == list(RESOLVE_STAGES_CANCELED), "赢家的声明生效"
    assert winner in engine.state.discard, "赢家那张真的用掉了"
    assert loser in engine.state.player(2).hand, "输家手牌张数不变（声明不耗牌）"
    assert loser not in engine.state.discard
    assert not [
        delta
        for delta in _deltas(engine, "card_move")
        if any(card.card_id == loser for card in delta.cards)
    ], "输家的牌没有任何移动（不耗牌、不进处理区）"
    assert len(_use_frames(engine)) == 2, "只有原锦囊 + 赢家两条使用事件"
    assert len(_declared(engine, loser)) == 1, "输家的声明被接受，但没有产生使用"
