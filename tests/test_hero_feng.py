"""风包 / 移动版：界曹仁（移动版）【据守】。

牌面（`D:\\SGS_HEROS\\神话再临-风包\\曹仁\\技能.txt`，用户录入）：

    据守：结束阶段，你可以摸三张牌，然后翻面。

三条验收（全走外部缝：询问 / 记录流 / 终局状态）：

1. **自己的结束阶段**问一次；发动 ⇒ 摸三张 + 武将牌翻到背面（各一条 ``card_move`` /
   ``flipped_change``）；
2. **不发动** ⇒ 一张不摸、状态不变；
3. **翻面是 toggle**：已经背面朝上时发动 ⇒ 翻回正面（不是"再置为背面"）。

翻面本身（跳过回合 / 不清理"直到你的下个回合开始"类作用域）在 ``tests/test_turn_loop.py``。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import ActivateSkill, EndPhase, Pass, Phase
from sgs.timing import Timing

from support import SeatSpec, build_state, run_queries

PACK = load_content_pack(deck_profile="standard_108")

JUSHSOU = "hero.jie_caoren_mobile.jushou"
HERO = "jie_caoren_mobile"


class Scripted:
    """是否发动【据守】由标志决定；其余询问走最小合法应答。"""

    def __init__(self, *, activate: bool) -> None:
        self.activate = activate
        self.queries = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == JUSHSOU:
            self.queries.append(query)
            if self.activate:
                return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return query.legal_actions[0]


def _run(*, activate: bool, flip_before_finish: bool = False, deck=tuple(range(10, 40))):
    """造一个"座位 0 是界曹仁移动版"的局面，跑到一个完整回合结束。

    ``flip_before_finish``：先在**出牌阶段开始后**把武将牌翻到背面（此刻"回合开始前"已经
    过去，故不会触发跳过），再看结束阶段的据守怎么把自己翻回来。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hand=(40, 41, 42)), SeatSpec()],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    player = Scripted(activate=activate)
    players = [player, Scripted(activate=False)]

    def stop(e) -> bool:
        return any(
            record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
            for record in e.records
        )

    if flip_before_finish:
        run_queries(
            engine,
            players,
            lambda e: any(
                record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
                for record in e.records
            ),
        )
        engine.set_flipped(None, 0, True)
    run_queries(engine, players, stop)
    return engine, player


def _draws(engine) -> list[int]:
    """座位 0 摸到的牌（按顺序）。"""
    return [
        card.card_id
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DRAW"
        and record.to_zone.seat == 0
        for card in record.cards
    ]


def _flips(engine) -> list[tuple[int, bool]]:
    return [
        (record.seat, record.flipped)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "flipped_change"
    ]


def test_jushou_draws_three_then_turns_the_hero_card_over():
    """正例：结束阶段问一次；发动 ⇒ 摸三张（牌堆顶三张）+ 翻面。"""
    engine, player = _run(activate=True)

    assert len(player.queries) == 1, "结束阶段问一次「是否发动」"
    assert player.queries[0].skill_id == JUSHSOU
    assert engine.state.player(0).flipped is True, "翻到背面朝上"
    assert _flips(engine) == [(0, True)], "翻面落一条 delta"
    # 摸牌阶段 2 张 + 结束阶段 3 张 = 5 张，且结束阶段那三张就是牌堆顶接下来的三张。
    drawn = _draws(engine)
    assert len(drawn) == 5, f"摸牌阶段 2 张 + 据守 3 张，实得 {drawn}"
    assert drawn[2:] == list(deck_top := list(range(10, 40))[2:5]), "据守摸的是牌堆顶三张"


def test_declining_jushou_leaves_hand_and_hero_card_untouched():
    """反例：不发动 ⇒ 一张不摸、不翻面。"""
    engine, player = _run(activate=False)

    assert len(player.queries) == 1
    assert engine.state.player(0).flipped is False
    assert _flips(engine) == [], "不发动不落翻面 delta"


def test_jushou_toggles_a_back_facing_hero_card_back_to_the_front():
    """翻面是 **toggle**（规则集 13.1）：背面朝上时发动 ⇒ 翻回正面。"""
    engine, player = _run(activate=True, flip_before_finish=True)

    assert len(player.queries) == 1
    assert engine.state.player(0).flipped is False, "背面 ⇒ 正面"
    assert _flips(engine) == [(0, True), (0, False)], "造局一条、据守一条"
