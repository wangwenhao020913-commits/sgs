"""座位级整局「使用 / 打出牌张数」``Player.used_or_played_ever``（框架票 03）。

口径（用户 2026-11-14 对票 01 的 Q1 / Q5 一次签，票 03 落地）：

- X **含**当前刚使用 / 打出的这一张（Q1 = A）——写点就在两个入口里，故触发点位靠前；
- **只数真实动作**（Q5 = A）：使用（真牌 / 转化产物 / 纯虚拟产物）与**打出**各 +1；
  **多源转化算 1 张**（【丈八蛇矛】两张手牌当【杀】使用仍是一次 ``start_use``）；
- 弃置 / 摸牌 / 判定 / 重铸 / 装备置入**不计**（它们走的是别的入口）；
- 座位级：谁的牌谁 +1；作用域 = **整局**（不随回合清、没有复位点）。
- **不发 delta、不进投影**：这里顺带钉住"记录流里没有它"（金录像机械判据的另一半）。

取数路径（``CODING_STANDARDS.md`` §6「读取方断言走真实产出路径」）：使用 / 打出走**真引擎
入口**（``start_use`` / ``play_card``），【丈八蛇矛】走**真的转化清单**（``PLAY_PHASE`` 询问的
``transform_preactivations`` + ``ActivateSkill``），不在这里改字段装作发生过。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    MoveReason,
    QueryKind,
    Reforge,
    WindowCause,
    Zone,
    ZoneRef,
    project,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")
SLASH = cards_of_type(PACK, "slash")[0]
DODGE = cards_of_type(PACK, "dodge")[0]
PEACH = cards_of_type(PACK, "peach")[0]
SERPENT = cards_of_type(PACK, "serpent_spear")[0]
"""丈八蛇矛：多源转化的唯一标准牌载体（两张手牌当【杀】）。"""


class _Scripted:
    """一个座位：先跑 ``handler``，返回 ``None`` 时退到最小应答（结束阶段 / 放弃）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = PassPlayer()

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _engine(seats, *, hand=(), seed: int = 11) -> tuple[Engine, object]:
    """人为局面 + 真引擎（``turn_seat=0``，座位 0 是第一个行动的座位）。"""
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
    )
    return Engine.from_state(state, seed=seed), state


def _used_ever(engine: Engine, seat: int = 0) -> int:
    return engine.state.player(seat).used_or_played_ever


def _settle(engine: Engine) -> Engine:
    """把已经压上的帧推到出栈（使用 / 打出各自的结算都走这里）。"""
    return run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: not e.stack.frames)


# ---------------------------------------------------------------------------
# 只数真实动作：使用 + 打出 = 2，弃置 / 摸牌 / 判定 / 重铸 / 装备置入不计
# ---------------------------------------------------------------------------


def test_used_or_played_ever_counts_use_and_play_but_not_discard():
    into_equip = cards_of_type(PACK, "crossbow")[0]
    engine, state = _engine(
        [SeatSpec(hand=(SLASH, DODGE, PEACH, into_equip), hp=4), SeatSpec(hp=4)],
        hand=(SLASH, DODGE, PEACH, into_equip),
    )
    assert _used_ever(engine) == 0, "开局：一张牌都还没用"

    engine.start_use(state.card(SLASH), source=0, targets=(1,))
    _settle(engine)
    assert _used_ever(engine) == 1, "使用一张 ⇒ 1（Q5：只数真实动作）"

    engine.play_card(state.card(DODGE), source=0, timing=Timing.PLAY_DECLARE)
    _settle(engine)
    assert _used_ever(engine) == 2, "打出是另一个入口、同样算 1 张"

    # 以下五种都不是"使用 / 打出"：它们走 move_in_frame / start_reforge，一条都不经过
    # start_use / play_card，故计数纹丝不动。
    deck_card = state.deck[0]
    engine.move_in_frame(
        None,
        state.refs((PEACH,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=0,
    )
    assert _used_ever(engine) == 2, "弃置不计"

    engine.move_in_frame(
        None,
        state.refs((deck_card,)),
        to_zone=ZoneRef(Zone.HAND, 0),
        reason=MoveReason.DRAW,
        owner=0,
        actor=0,
    )
    assert _used_ever(engine) == 2, "摸牌不计"

    engine.move_in_frame(
        None,
        state.refs((state.deck[0],)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.JUDGE,
        owner=None,
    )
    assert _used_ever(engine) == 2, "判定（牌进处理区）不计"

    engine.start_reforge(
        Reforge(card=state.card(deck_card), answers_seq=1),
        source=0,
    )
    _settle(engine)
    assert _used_ever(engine) == 2, "重铸不是使用（M5-03：不算使用锦囊、不进 use count）"

    # 装备置入（PUT）同样不是使用 / 打出：把手上那张装备牌直接置入装备区。
    engine.move_in_frame(
        None,
        state.refs((into_equip,)),
        to_zone=ZoneRef(Zone.EQUIP, 0),
        reason=MoveReason.PUT,
        owner=0,
        actor=0,
    )
    assert _used_ever(engine) == 2, "装备置入不计"

    assert not any(
        record.kind == "delta"
        and getattr(record, "delta_type", "") in {"used_or_played_change", "card_usage"}
        for record in engine.records
    ), "新字段绝不发 delta（发了就会改 47 条金录像的字节）"


# ---------------------------------------------------------------------------
# 多源转化算 1 张（【丈八蛇矛】两张手牌当【杀】）
# ---------------------------------------------------------------------------


def test_used_or_played_ever_counts_a_multi_source_transform_as_one():
    first, second = cards_of_type(PACK, "peach")[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,), hp=4), SeatSpec(hp=4)],
        deck=deck_excluding(PACK, first, second, SERPENT),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=37)

    def attacker(query):
        if query.kind != "PLAY_PHASE":
            return None
        # W4-fix-2：转化候选已从 legal_actions 收回，按**清单**确认（服务端解析成那条
        # 普通的 `UseCard(虚拟牌, 目标)`）——这正是技能侧喂给 start_use 的那个产物。
        steps = query.transform_preactivations.get("equip.serpent_spear")
        if not steps:
            return None
        target = steps[1].pool[0].options[0].removeprefix("seat:")
        return ActivateSkill(
            skill_id="equip.serpent_spear",
            picks=(first, second),
            choices=(f"seat:{target}",),
            answers_seq=query.seq,
        )

    run_queries(
        engine,
        [_Scripted(attacker), PassPlayer()],
        lambda e: ran_timing(e, Timing.DAMAGE_END),
    )

    assert _used_ever(engine) == 1, (
        "两张来源牌换来的仍是**一次**使用动作 ⇒ 1 张（Q5：多源转化算 1）"
    )
    assert engine.state.player(1).hp == 3, "前置：这次转化【杀】真的命中了（不是取消）"
    both = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [first, second]
    ]
    assert both, "前置：两张来源牌一起被搬走（多源转化的牌面事实）"


# ---------------------------------------------------------------------------
# 座位级 + 整局作用域（不随回合边界复位）
# ---------------------------------------------------------------------------


def test_used_or_played_ever_is_per_seat_and_survives_the_turn_boundary():
    engine, state = _engine(
        [SeatSpec(hand=(SLASH,), hp=4), SeatSpec(hand=(DODGE,), hp=4)],
        hand=(SLASH, DODGE),
    )

    engine.start_use(state.card(SLASH), source=0, targets=(1,))
    _settle(engine)
    assert (_used_ever(engine, 0), _used_ever(engine, 1)) == (1, 0), "座位级：只有使用者 +1"

    engine.play_card(state.card(DODGE), source=1, timing=Timing.PLAY_DECLARE)
    _settle(engine)
    assert (_used_ever(engine, 0), _used_ever(engine, 1)) == (1, 1), (
        "打出者自己 +1（别人的回合里打出也算，Q5 不看回合 / 阶段）"
    )

    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.TURN_END),
    )

    assert (_used_ever(engine, 0), _used_ever(engine, 1)) == (1, 1), (
        "回合结束不清：作用域 = 整局（与 damage_taken_ever 同姿态，不在 _clear_turn_effects 里）"
    )


# ---------------------------------------------------------------------------
# 信息通道不变：不进投影（读取方断言走真实产出路径：`sgs.state.project`）
# ---------------------------------------------------------------------------


def test_the_counter_stays_out_of_the_participant_projection():
    engine, _ = _engine([SeatSpec(hp=4), SeatSpec(hp=4)])
    # 直接写大值：若投影里有它，下面一定看得见（这是"读者"那一侧的判据，不是写点判据）。
    engine.state.player(0).used_or_played_ever = 7

    payload = project(engine.state, 0).model_dump(mode="json")

    assert "used_or_played_ever" not in payload, "整局张数是座位私有事实，不进 `PlayerView`"
    assert all(
        "used_or_played_ever" not in row for row in payload["players"]
    ), "也不进 `PlayerPublic`（信息通道与 47 条金录像的字节都不变）"


# ---------------------------------------------------------------------------
# 阵亡后仍在飞的使用照常计数（dead-actor-in-flight 票 02：无中）
# ---------------------------------------------------------------------------


class _DieInNullify:
    """让 ``victim`` 在**无懈窗口**阵亡（产品死亡入口）；其余询问退到 ``PassPlayer``。"""

    def __init__(self, engine: Engine, victim: int) -> None:
        self._engine = engine
        self._victim = victim
        self._fallback = PassPlayer()
        self.died = False

    def respond(self, query):
        if (
            not self.died
            and query.kind == QueryKind.RESPOND
            and getattr(query, "cause", None) is WindowCause.NULLIFY
        ):
            self._engine.enter_death(self._engine.stack.top, self._victim)
            self.died = True
        return self._fallback.respond(query)


def test_a_dead_ex_nihilo_user_draws_nothing_and_the_card_still_discards():
    """无中：目标（自己）已死 ⇒ 无合法目标、不摸牌；牌照常进弃牌堆。

    同时钉住"使用已经发生"：``used_or_played_ever`` 的唯一写点是 ``start_use``，随后的
    阵亡不撤销这次使用——死亡不能反向改写它（票 02 的牌侧与使用计数交界）。
    """
    ex_nihilo = cards_of_type(PACK, "ex_nihilo")[0]
    engine, state = _engine(
        [SeatSpec(hand=(ex_nihilo,), hp=4), SeatSpec(hp=4), SeatSpec(hp=4)],
        hand=(ex_nihilo,),
    )
    deck_before = len(state.deck)
    engine.start_use(state.card(ex_nihilo), source=0, targets=(0,))

    run_queries(
        engine,
        [_DieInNullify(engine, 0), PassPlayer(), PassPlayer()],
        lambda e: not e.stack.frames,
    )

    assert _used_ever(engine) == 1, "使用发生在死亡之前，死亡不撤销它"
    assert not [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DRAW
    ], "目标（自己）已阵亡 ⇒ 无合法目标、不摸牌"
    assert len(state.deck) == deck_before, "牌堆一张都没少"
    assert ex_nihilo in state.discard, "使用牌照常进弃牌堆"
    assert state.player(0).alive is False
    assert state.player(0).hand == [], "死者的手牌区为空"
