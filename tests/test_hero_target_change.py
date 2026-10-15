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
from sgs.state import ActivateSkill, EndPhase, Pass, Zone
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
    """座位 0 使用【杀】；座位 1（大乔）发动【流离】并把目标改到座位 2；其余放弃。

    票 08 把【流离】改成两步流水线后，两问的形状分别是：成本 = ``SELECT_CARDS``（自己的
    手牌 / 装备区，面内**牌面可见**）、新目标 = ``SELECT_OPTION``（选项 ``seat:N``，不再用
    座位槽）。驱动器按形状分别记录，供三条行为基线读。
    """

    def __init__(self, *, activate: bool = True, new_target: int = 2) -> None:
        self.activate = activate
        self.new_target = new_target
        self.activation_queries = []
        self.cost_queries = []
        self.target_queries = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == LIULI:
            self.activation_queries.append(query)
            if not self.activate:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
        if query.kind == "SELECT_CARDS" and query.actor == 1:
            # 步 1 = 成本：面内牌面可见（reveal_hand=True），直接按 PAID 选那一张。
            self.cost_queries.append(query)
            return next(
                a
                for a in query.legal_actions
                if a.picks
                and query.slots[a.picks[0]].card is not None
                and query.slots[a.picks[0]].card.card_id == PAID
            )
        if query.kind == "SELECT_OPTION" and query.actor == 1:
            # 步 2 = 新目标：座位选项 ``seat:N``（票 08 的有意交互变更）。
            self.target_queries.append(query)
            return next(
                a
                for a in query.legal_actions
                if getattr(a, "choice", None) == f"seat:{self.new_target}"
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

    这一局里只有座 2 满足，故这一步的候选座位恰好只有座 2。判据（候选集合 == {2}）
    与迁移前一字不差，只是承载它的询问从"座位槽的 ``SELECT_CARDS``"换成了
    ``SELECT_OPTION`` 的 ``seat:N``（票 08 的有意交互变更）——故这里读 ``options`` 而不是
    ``slots``。
    """
    engine = _liuli_engine()
    driver = _LiuliDriver()

    run_queries(engine, [driver, driver, _PASS], lambda e: _hit(e, 2))

    assert len(driver.target_queries) == 1, "选新目标问一次"
    seats = {
        int(option.removeprefix("seat:"))
        for query in driver.target_queries
        for option in query.options
    }
    assert seats == {2}


# ---------------------------------------------------------------------------
# 票 08：两步流水线（成本 = SELECT_CARDS、新目标 = SELECT_OPTION 的 seat:N）
# ---------------------------------------------------------------------------


def test_liuli_flow_is_two_mandatory_steps_without_any_extra_cancel_exit():
    """声明形状：``PickCards`` → ``PickSeats``，两步都 ``optional=False``。

    ``optional=False`` 是**有意保留今天的取消出口**（票 08 明写）：迁移前的两步都没有
    ``Pass``，取消的唯一出口是发动询问那一道。顺手给一个 ``Pass`` 会新增取消出口、
    改记录流与既有录像。
    """
    from sgs.skills.flow import PickCards, PickSeats, SkillFlow
    from sgs.skills.target_change import LIULI_FLOW, _liuli_target_pool

    assert isinstance(LIULI_FLOW, SkillFlow)
    cost, target = LIULI_FLOW.steps
    assert isinstance(cost, PickCards)
    assert cost.zones == (Zone.HAND, Zone.EQUIP)
    assert (cost.lo, cost.hi) == (1, 1)
    assert cost.optional is False, "成本步不给取消出口（取消只有发动询问那一道）"
    assert cost.key == "card"

    assert isinstance(target, PickSeats)
    assert (target.lo, target.hi) == (1, 1)
    assert target.optional is False, "目标步同样不给取消出口"
    assert target.key == "seat"
    assert target.pool is _liuli_target_pool, "新目标池继续走同一条派生（排除使用者与自己）"


def test_liuli_asks_the_new_target_as_a_seat_option():
    """**票 08 的用户可见交互变更**：第 2 步是 ``SELECT_OPTION``、选项形如 ``seat:N``。

    与 A 族【离间】/【青囊】/【结姻】同一条约定（``seat:N`` 让前端直接高亮场上武将、
    不铺选项按钮）；迁移前它是 ``SELECT_CARDS`` 上的**座位槽**。
    """
    engine = _liuli_engine()
    driver = _LiuliDriver()

    run_queries(engine, [driver, driver, _PASS], lambda e: _hit(e, 2))

    assert driver.cost_queries and all(
        query.kind == "SELECT_CARDS" for query in driver.cost_queries
    ), "步 1 仍是选牌的 SELECT_CARDS"
    assert len(driver.target_queries) == 1, "步 2 恰好问一次"
    query = driver.target_queries[0]
    assert query.kind == "SELECT_OPTION", "步 2 是座位选项询问（票 08 的交互变更）"
    assert query.options == ("seat:2",), "候选以 seat:N 表达，且只有合法新目标"
    assert query.skill_id == "liuli:seat", "步骤键 = seat（逐步文案按它取）"


def test_declining_liuli_asks_nothing_after_the_activation_offer():
    """取消出口只有发动询问那一道：答 ``Pass`` ⇒ 零副作用、两问一个都不发。"""
    engine = _liuli_engine()
    driver = _LiuliDriver(activate=False)

    run_queries(engine, [driver, driver, _PASS], lambda e: _hit(e, 1))

    assert len(driver.activation_queries) == 1
    assert driver.cost_queries == [], "不发动 ⇒ 不问成本"
    assert driver.target_queries == [], "不发动 ⇒ 不问新目标"
    assert PAID in engine.state.player(1).hand, "手牌没动"
    assert PAID not in engine.state.discard, "不发动 ⇒ 不付成本"


# ---- 成本搬牌内联挂起：``liuli:cost_moved`` 帧内游标（票 08 §要做什么）----------


class _StubStack:
    depth = 0


class _EffectHost:
    """只服务 :func:`_liuli_effect` 的**最小宿主**：搬牌可脚本化成"先挂起一次"。

    真实的 ``move_in_frame`` 在 ``MOVE_AFTER`` 钩子提问时返回 ``"stay"``，而【流离】的
    持有者（大乔）不可能同时是【连营】/【枭姬】的持有者，故**造不出一条经过真实引擎的
    端到端挂起局**（现役两条 ``MOVE_AFTER`` 技能只同步摸牌、不提问）。这里直接钉效果体
    对 ``"stay"`` 的透传与游标语义——迁移前那段代码的唯一判据。
    """

    def __init__(self, state, *, suspend_first: bool) -> None:
        self.state = state
        self.stack = _StubStack()
        self.suspend_first = suspend_first
        self.move_calls = 0
        self.emits: list[tuple[Timing, dict]] = []

    def move_in_frame(self, frame, refs, **kwargs):
        self.move_calls += 1
        return "stay" if self.suspend_first and self.move_calls == 1 else None

    def emit_timing(self, timing, **kwargs):
        self.emits.append((timing, kwargs))


class _EffectFrame:
    def __init__(self, **data) -> None:
        self.data = data
        self.frame_id = 7


def test_liuli_cost_move_that_suspends_is_resumed_without_moving_twice():
    """成本搬牌挂起 ⇒ ``"stay"`` 原样透传；重入跳过搬牌，直接落转移与补派发。"""
    from sgs.skills.target_change import _LIULI_USED, _liuli_effect

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec(hero="daqiao")])
    frame = _EffectFrame(
        source=0, skill_owner=1, targets=(1,), card=state.card(SLASH)
    )
    answers = {"card": (PAID,), "seat": (2,)}
    host = _EffectHost(state, suspend_first=True)

    assert _liuli_effect(host, frame, answers) == "stay", "挂起原样透传给引擎"
    assert frame.data["liuli:cost_moved"] is True, "先落'已搬'游标"
    assert frame.data.get(_LIULI_USED) is not True, "还没走完，转移尚未发生"

    assert _liuli_effect(host, frame, answers) == "stay"
    assert host.move_calls == 1, "重入**不再搬第二遍**（游标生效）"
    assert frame.data[_LIULI_USED] is True, "重入把转移走完"
    assert frame.data["targets"] == (2,), "自己那一项被换成新目标"
    assert len(host.emits) == 1
    timing, payload = host.emits[0]
    assert timing is Timing.USE_BECOME_TARGET, "给新目标补发一次『成为目标时』"
    assert payload["actor"] == 2 and payload["targets"] == (2,), (
        "载荷显式给 targets=(new_target,)（与 use_become_target 重入那一轮的键一致）"
    )
