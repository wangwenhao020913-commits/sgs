"""K 族【马术】与 M 族【流离】的构造局面单测（票 12 登记）。

两条技能都不是新机制：马术是 ``Modifier(DISTANCE, −1)``（与 −1 坐骑同一条链），
流离把「成为目标时」时机上的目标**转移**给攻击范围内的另一名角色（票 12 §落地表第 11/22 行）。
判据全走外部缝：``distance`` 的只读查询、记录流里的目标列表、体力变化与实体牌去向。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.effects import distance
from sgs.skills import CATALOG
from sgs.state import ActivateSkill, EndPhase, Pass
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, cards_of_type, run_queries

PACK = load_content_pack(deck_profile="standard_108")

MASHU = "hero.machao.mashu"
LIULI = "hero.daqiao.liuli"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


# ---------------------------------------------------------------------------
# 【马术】：与其他角色的距离 −1（下限 1、自身 0）
# ---------------------------------------------------------------------------


def _seat_ring(*, hero: str | None, seats: int = 5):
    specs = [SeatSpec(hero=hero)] + [SeatSpec() for _ in range(seats - 1)]
    return build_state(pack=PACK, seats=specs)


def test_mashu_reduces_the_distance_from_its_owner_by_one():
    """五座环上 0↔2 的座位距离是 2；马超在座 0 ⇒ 1（与 −1 坐骑同一条链）。"""
    with_hero = _seat_ring(hero="machao")
    without = _seat_ring(hero=None)

    assert distance(without, 0, 2) == 2
    assert distance(with_hero, 0, 2) == 1


def test_mashu_only_applies_to_the_owner_as_the_subject():
    """「你与其他角色的距离 −1」是**主体侧**的修正：别人到你不变、你到别人减一。

    判据 = 同一对座位换方向读：马超（座 0）到座 2 是 1、座 2 到马超仍是 2。
    """
    state = _seat_ring(hero="machao")

    assert distance(state, 0, 2) == 1
    assert distance(state, 2, 0) == 2


def test_mashu_never_drops_the_distance_below_one():
    """相邻两人本来就是 1：修正后由管道的下限夹住（规则"最小为 1"）。"""
    state = _seat_ring(hero="machao")

    assert distance(state, 0, 1) == 1


def test_mashu_is_locked_and_carries_only_a_distance_modifier():
    definition = CATALOG[MASHU]

    assert definition.locked is True
    assert [modifier.point.value for modifier in definition.modifiers] == ["DISTANCE"]
    assert definition.hooks == ()


# ---------------------------------------------------------------------------
# 【流离】：弃一张牌，把【杀】转移给攻击范围内的另一名角色
# ---------------------------------------------------------------------------

SLASH = _cards("slash")[0]
PAID = _cards("slash")[1]
DODGE = _cards("dodge")[0]


class _LiuliDriver:
    """座位 0 使用【杀】；座位 1（大乔）发动【流离】并把目标改到座位 2；其余放弃。"""

    def __init__(self, *, activate: bool = True, new_target: int = 2) -> None:
        self.activate = activate
        self.new_target = new_target
        self.activation_queries = []
        self.target_queries = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == LIULI:
            self.activation_queries.append(query)
            if not self.activate:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
        if query.kind == "SELECT_CARDS" and query.actor == 1:
            # 两次选牌：先付成本（弃一张手牌），再选新目标（面内位置 = 座位槽）。
            if any(
                slot.card is not None and slot.card.card_id == PAID for slot in query.slots
            ):
                return next(
                    a
                    for a in query.legal_actions
                    if a.picks
                    and query.slots[a.picks[0]].card is not None
                    and query.slots[a.picks[0]].card.card_id == PAID
                )
            self.target_queries.append(query)
            return next(
                a
                for a in query.legal_actions
                if a.picks and query.slots[a.picks[0]].ref.seat == self.new_target
            )
        if query.kind == "PLAY_PHASE" and query.actor == 0:
            for action in query.legal_actions:
                card = getattr(action, "card", None)
                if card is not None and card.card_id == SLASH:
                    return action
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _liuli_engine() -> Engine:
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,)),
            SeatSpec(hero="daqiao", hand=(PAID,)),
            SeatSpec(),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(PACK.deck.cards))
            if card_id not in (SLASH, PAID, DODGE)
        ),
        turn_seat=0,
    )
    return Engine.from_state(state, seed=11)


_PASS = PassPlayer()


def _hit(engine, seat: int) -> bool:
    return any(
        record.kind == "delta"
        and record.delta_type == "hp_change"
        and record.seat == seat
        for record in engine.records
    )


def _become_target_targets(engine) -> list[tuple[int, ...]]:
    return [
        tuple(record.targets)
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.USE_BECOME_TARGET
    ]


def test_liuli_transfers_the_slash_to_another_character_in_range():
    engine = _liuli_engine()
    driver = _LiuliDriver()

    run_queries(engine, [driver, driver, _PASS], lambda e: _hit(e, 2))

    assert len(driver.activation_queries) == 1, "成为【杀】的目标时问一次「是否发动」"
    # 记录流：大乔先"成为目标"（转移就发生在这个时机），换过的目标再拿到一次"成为目标"。
    assert _become_target_targets(engine)[-1] == (2,)
    assert engine.state.player(1).hp == 4, "大乔不再结算此【杀】"
    assert engine.state.player(2).hp == 3, "被转移的目标结算此【杀】"
    assert PAID in engine.state.discard, "弃一张牌是【流离】的成本"


def test_declining_liuli_keeps_the_original_target():
    engine = _liuli_engine()
    driver = _LiuliDriver(activate=False)

    run_queries(engine, [driver, driver, _PASS], lambda e: _hit(e, 1))

    assert len(driver.activation_queries) == 1
    assert engine.state.player(1).hp == 3
    assert engine.state.player(2).hp == 4
    assert PAID not in engine.state.discard, "不发动 ⇒ 不付成本"


def test_liuli_excludes_the_user_and_the_owner_from_the_new_target_pool():
    """新目标 = 大乔攻击范围内、除去此【杀】使用者（座 0）与大乔自己（座 1）。

    这一局里只有座 2 满足，故选择面的候选位置恰好落在座 2。
    """
    engine = _liuli_engine()
    driver = _LiuliDriver()

    run_queries(engine, [driver, driver, _PASS], lambda e: _hit(e, 2))

    assert len(driver.target_queries) == 1, "选新目标问一次"
    seats = {
        query.slots[action.picks[0]].ref.seat
        for query in driver.target_queries
        for action in query.legal_actions
        if action.picks
    }
    assert seats == {2}
