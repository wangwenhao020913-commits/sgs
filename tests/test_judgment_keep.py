"""框架票 02：判定牌**留在处理区**（``open_skill_judgment(..., keep_card=True)``）。

规范来源 = 用户 2026-11-14 口径：「判定过程中这些判定牌一直留在效果区，直到出现相同花色，
判定流程终止，神郭嘉获得这些判定牌」——首个消费者 = 神郭嘉【慧识】。

三条钉住的东西：

1. **缺省**（不传 ``keep_card``）的判定仍按规则 11.2 入弃牌堆——既有行为逐字节不变；
2. ``keep_card=True`` 的判定在 ``judge_finish`` 之后仍**留在处理区（= 效果区）**；
3. 改判只影响"最终生效的那张"：被替下的原牌按 ``rejudge_original_to`` 走，留在处理区的
   是新牌。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import (
    FrameKind,
    GameState,
    Pass,
    SubstituteJudgeCard,
    WindowCause,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, deck_excluding

PACK = load_content_pack(deck_profile="standard_108")


def _judge_data(*, keep: bool) -> dict:
    """与 :meth:`Engine.open_skill_judgment` 压出的 JUDGE 帧逐键同形（``after=None``
    ⇒ 不调任何判定后果，本测试只关心判定牌的去向）。"""
    data = {
        "subject": 0,
        "targets": (0,),
        "source": None,
        "card": None,
        "judge_card": None,
        "use_timing": None,
        "after": None,
        "skill_id": "test.judge",
        "activation_parent": 0,
    }
    if keep:
        data["keep_judge_card"] = True
    return data


def _run_judgment(state: GameState, *, keep: bool, players=None) -> tuple[Engine, int]:
    """压一个 JUDGE 帧并把它跑完；返回（引擎, 判定牌 id）。"""
    engine = Engine.from_state(state, seed=7)
    engine._step()  # L0（游戏开始时）先落，帧序与真实对局一致
    engine.push_frame(FrameKind.JUDGE, _judge_data(keep=keep), timing=Timing.JUDGE_ON)
    judge_card = state.deck[0]
    for _ in range(200):
        if engine.pending is None:
            if not engine.stack:
                break
            engine._step()
            continue
        query = engine.pending
        responder = players[query.actor] if players is not None else None
        engine.submit(
            responder.respond(query) if responder is not None else _pass(query)
        )
    return engine, judge_card


def _pass(query):
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def _base_state(seats: list[SeatSpec]) -> GameState:
    return build_state(pack=PACK, seats=seats, deck=deck_excluding(PACK))


# ---------------------------------------------------------------------------
# ① 缺省：判定牌照旧入弃牌堆
# ---------------------------------------------------------------------------


def test_a_default_judgment_still_discards_the_card():
    state = _base_state([SeatSpec(), SeatSpec()])
    engine, judge_card = _run_judgment(state, keep=False)

    assert judge_card not in state.processing, "缺省判定不得留在处理区"
    assert judge_card in state.discard, "缺省判定牌必须按规则 11.2 入弃牌堆"


# ---------------------------------------------------------------------------
# ② keep_card：判定牌留在处理区（= 效果区），由技能自己搬走
# ---------------------------------------------------------------------------


def test_a_kept_judge_card_stays_in_processing_until_the_skill_moves_it():
    state = _base_state([SeatSpec(), SeatSpec()])
    engine, judge_card = _run_judgment(state, keep=True)

    assert judge_card in state.processing, "keep_card 的判定牌必须留在处理区"
    assert judge_card not in state.discard, "keep_card 的判定牌不得进弃牌堆"
    assert state.card(judge_card).type is not None


# ---------------------------------------------------------------------------
# ③ 改判：留在处理区的是最终生效的那张；原牌按代替 / 替换走
# ---------------------------------------------------------------------------


class _Substitute:
    """司马懿：改判窗口里用指定那张手牌代替；其余询问走最小合法应答。"""

    def __init__(self, card_id: int) -> None:
        self._card_id = card_id

    def respond(self, query):
        if query.kind == "RESPOND" and query.cause is WindowCause.REJUDGE:
            for action in query.legal_actions:
                if (
                    isinstance(action, SubstituteJudgeCard)
                    and action.card.card_id == self._card_id
                ):
                    return action
        return _pass(query)


def test_a_rejudged_card_keeps_only_the_final_card():
    replacement = next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in (0,)
    )
    state = _base_state([SeatSpec(hero="simayi", hand=(replacement,)), SeatSpec()])
    engine, original = _run_judgment(
        state, keep=True, players=[_Substitute(replacement), _Substitute(replacement)]
    )

    assert replacement in state.processing, "最终生效的判定牌必须留在处理区"
    assert original not in state.processing, "被替下的原判定牌不得留在处理区"
    assert original in state.discard, "【鬼才】是「代替」：原判定牌入弃牌堆"
