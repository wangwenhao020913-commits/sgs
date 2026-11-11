"""票 07：被动技步骤流水线原语 + 【反馈】迁上 :class:`~sgs.skills.flow.SkillFlow`。

本文件只钉**票 07 新增的那一层**（原语本身的既有行为在 ``tests/test_hero_active.py``；
【反馈】的"行为逐字节等价"主证据是 ``tests/test_hero_damage_after.py`` 的 4 条既有用例，
它的断言一个字都不改）：

1. :class:`~sgs.skills.catalog.Hook` / :class:`~sgs.skills.catalog.Seam` 的
   "``handler`` 与 ``flow`` **恰有其一**"（与 ``ActiveSkill.__post_init__`` 同一条纪律）；
2. ``run_flow`` 的 ``owner`` / ``timing`` 显式覆盖在**真实被动技**上生效——被动侧读的是
   ``skill_owner``（技能持有者）而**不是** ``frame["source"]``（伤害来源），询问挂的时机是
   派发环当前那个（``DAMAGE_AFTER_TAKE``）而不是主动技的 ``PHASE_PLAY_IDLE``；
3. 【反馈】的**形状**：一条一步流水线，选取面 = 伤害来源的区、应答者 = 持有者；
4. 【反馈】的选取每点各问一次（同一次伤害的多点派发不共用缓存答案）。

第 2 条的夹具刻意造"**来源 ≠ 持有者**"的帧（司马懿在座位 1 挨打、伤害来源是座位 0）：
把 ``source`` 当持有者读的实现会把选择权交给座位 0，断言立刻变红。
"""

from __future__ import annotations

from itertools import permutations

import pytest

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.effects import EffectContext, legal_actions_for, move_card
from sgs.skills import Hook, Seam, catalog as catalog_module
from sgs.skills.active import ActiveSkill
from sgs.skills.preactivation import _build_flows, preactivated_skill_ids
from sgs.skills.damage_after import FANKUI_FLOW, fankui_step_key
from sgs.skills.flow import (
    Arrange,
    DeckTopArrange,
    FixedSplit,
    PickCards,
    PickOption,
    PickProvide,
    PickSeats,
    SkillFlow,
    _flow_step_cards,
    _flow_step_option,
    manifest,
    run_flow,
    validate_flow_activation,
    watched_cards,
)
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    ArrangeContext,
    FrameKind,
    MoveReason,
    Pass,
    QueryKind,
    SelectCards,
    SelectCardsQuery,
    Slot,
    SlotKind,
    Step,
    StepPool,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_108")

FANKUI = "hero.simayi.fankui"

DAMAGE_AFTER_TAKE = Timing.DAMAGE_AFTER_TAKE


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


class _Frame:
    """最小的帧替身：派生参数只读 ``frame.data``。"""

    def __init__(self, **data) -> None:
        self.data = data


def _frame_with_point(point: int) -> _Frame:
    return _Frame(source=0, damage_point_index=point)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


# ---------------------------------------------------------------------------
# ① Hook / Seam：handler 与 flow 恰有其一（形状校验）
# ---------------------------------------------------------------------------


def _noop(host, frame):
    return None


def _noop_flow() -> SkillFlow:
    return SkillFlow(
        name="noop",
        steps=(PickCards(zones=(Zone.HAND,), lo=1, hi=1),),
        effect=_noop,
    )


def test_a_hook_requires_exactly_one_of_handler_and_flow():
    """``Hook(handler=…)`` / ``Hook(flow=…)`` 各合法；两者都给或都不给都判错。

    与 :meth:`~sgs.skills.active.ActiveSkill.__post_init__` 同一条纪律：声明面只有一份真源，
    不会出现"回调与声明各写一套"的漂移。**都不给**那一档同样要拦——否则派发环会把
    ``None`` 当回调调、在运行时才炸。
    """
    Hook(Timing.DAMAGE_AFTER_TAKE, _noop)
    Hook(Timing.DAMAGE_AFTER_TAKE, flow=_noop_flow())

    with pytest.raises(ValueError):
        Hook(Timing.DAMAGE_AFTER_TAKE, _noop, flow=_noop_flow())
    with pytest.raises(ValueError):
        Hook(Timing.DAMAGE_AFTER_TAKE)


def test_a_seam_requires_exactly_one_of_handler_and_flow():
    """接缝与钩子同一纪律（两处声明，一条判据）。"""
    kind = catalog_module.SeamKind.SLASH_CANCELED
    Seam(kind, _noop)
    Seam(kind, flow=_noop_flow())

    with pytest.raises(ValueError):
        Seam(kind, _noop, flow=_noop_flow())
    with pytest.raises(ValueError):
        Seam(kind)


# ---------------------------------------------------------------------------
# ② 一次性夹具：座位 0 = 伤害来源，座位 1 = 挨打的司马懿（"来源 ≠ 持有者"）
# ---------------------------------------------------------------------------


class _Scripted:
    """技能发动一律同意；选牌取面内第一个槽位；其余取候选里第一个。"""

    def __init__(self) -> None:
        self.selections: list[SelectCardsQuery] = []

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION:
            for action in query.legal_actions:
                if getattr(action, "kind", None) == "ActivateSkill":
                    return action
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == QueryKind.SELECT_CARDS:
            self.selections.append(query)
            return next(
                a for a in query.legal_actions if isinstance(a, SelectCards) and a.picks == (0,)
            )
        return query.legal_actions[0]


def _damage_frame(state, source: int | None, *, amount: int = 1) -> dict:
    """与引擎同形的 ``DAMAGE`` 子帧载荷（同 ``test_hero_damage_after._damage_frame``）。"""
    slash = _cards("slash")[0]
    channel = state.card(slash)
    return {
        "source": source,
        "targets": (1,),
        "card": channel,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": 1,
        "channel": channel,
        "nature": "NORMAL",
        "chain": False,
        "amount": amount,
    }


def _run_damage(*, source_hand: tuple[int, ...], amount: int = 1):
    """跑完一个 ``DAMAGE`` 子帧（座位 0 打座位 1 的司马懿）；返回 ``(engine, 司马懿应答器)``。"""
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=source_hand), SeatSpec(hero="simayi")],
        deck=_deck_excluding(slash, *source_hand),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, 0, amount=amount),
        timing=Timing.DAMAGE_DEAL,
    )
    simayi = _Scripted()
    players = [_Scripted(), simayi]
    for _ in range(20_000):
        if any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in engine.records
        ):
            return engine, simayi
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        # 夹具是"座位 1 = 司马懿"：**不按** ``query.actor`` 挑应答器——那会让本测试要观察的
        # 选牌询问落进座位 0 的应答器，断言看到 0 次；这里让持有者那一档专门记选牌。
        engine.submit((simayi if query.actor == 1 else players[0]).respond(query))
    raise AssertionError("步数上限内没有跑到 DAMAGE_END")


# ---------------------------------------------------------------------------
# ③ run_flow 的 owner / timing 覆盖（在真实【反馈】上读）
# ---------------------------------------------------------------------------


def test_fankui_asks_the_skill_owner_at_the_dispatch_timing():
    """【反馈】的选牌询问落在**持有者**（座位 1）上、挂**派发环的时机**。

    这是票 07 §2 的核心判据，也是"被动技不能读 ``frame['source']``"的机械证明：
    本局 ``source`` 是座位 0（伤害来源），持有者是座位 1（司马懿）。读错就把选择权
    交给了伤害来源；时机写死 ``PHASE_PLAY_IDLE`` 则与迁移前的记录流不一致。
    """
    engine, simayi = _run_damage(source_hand=(_cards("peach")[0],))

    assert len(simayi.selections) == 1, "【反馈】只问一次选牌"
    query = simayi.selections[0]
    assert query.actor == 1, "被动侧读 skill_owner（座位 1），不是帧的 source（座位 0）"
    assert query.timing is DAMAGE_AFTER_TAKE, "询问挂派发环当前的时机，不写死出牌阶段空闲"


def test_run_flow_needs_an_integer_owner_and_reads_the_frame_source_by_default():
    """``run_flow(owner=…)`` 的取值收成 ``int``：缺省读帧 ``source``，传 ``None`` 立刻炸。

    票 12 §2 的二选一：旧文档写"``owner=None`` 合法（应答者按步骤的 ``actor`` 定）"，而实现
    ``int(... else owner)`` 对 ``None`` 直接 ``TypeError`` —— 文档与实现互相打架，且全仓无
    ``owner=None`` 调用点。本票选**收紧**（"真支持"要在每次发问前新增"``actor`` 必填"的校验
    层，没有消费者），故类型与文档收成 ``int``，并在这里把契约钉死：未指定读帧的 ``source``、
    显式座位覆盖它、显式 ``None`` 是编程错误。
    """
    slash = _cards("slash")[0]
    peach = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(peach,))],
        deck=_deck_excluding(slash, peach),
        turn_seat=0,
    )
    seen: dict[str, object] = {}

    class _Host:
        def __init__(self) -> None:
            self.state = state

        @staticmethod
        def suspended(value) -> bool:
            return value == "SUSPEND"

        @staticmethod
        def accepted_selection(frame, *, owner, key):
            del frame, owner, key
            return None  # 夹具不模拟续跑：这一问还没答过

        @staticmethod
        def selection_face(seat, zones, *, reveal_hand):
            del reveal_hand
            slots, card_ids = [], []
            for zone in zones:
                for card_id in state.zone(ZoneRef(zone, seat)):
                    slots.append(Slot(ref=ZoneRef(zone, seat), card=state.card(card_id)))
                    card_ids.append(card_id)
            return tuple(slots), tuple(card_ids)

        @staticmethod
        def ask_selection(
            frame, *, owner, key, actor, timing, spec, build_face,
            revealed=(), ask_when_empty=False,
        ):
            del frame, owner, key, timing, spec
            build_face()
            seen["actor"] = actor
            return (0,)

    flow = SkillFlow(
        name="probe_actor",
        steps=(PickCards(zones=(Zone.HAND,), lo=1, hi=1, optional=False),),
        effect=lambda host, frame, answers: None,
    )

    run_flow(_Host(), _Frame(source=1), flow)
    assert seen["actor"] == 1, "缺省（未指定）读帧的 source"

    run_flow(_Host(), _Frame(source=0), flow, owner=1)
    assert seen["actor"] == 1, "显式 owner 覆盖帧的 source"

    with pytest.raises(TypeError):
        run_flow(_Host(), _Frame(source=0), flow, owner=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ④ 【反馈】的声明形状：一条一步流水线，面/应答者都指回伤害来源
# ---------------------------------------------------------------------------


def test_fankui_flow_is_a_single_card_pick_on_the_damage_source():
    """``PickCards(HAND ∪ EQUIP, 恰好 1 张)``，``actor`` 与 ``face`` 都派生自**帧里的来源**。

    ``optional=False`` 是**有意保留现状**（票 07 §5）：迁移前这一步没有 ``Pass``，
    取消出口只有"是否发动"那一道；改成 ``True`` 会改记录流与既有录像。
    """
    assert isinstance(FANKUI_FLOW, SkillFlow)
    assert len(FANKUI_FLOW.steps) == 1, "【反馈】只有选牌一步（目标发动前已定 = 来源）"
    step = FANKUI_FLOW.steps[0]
    assert isinstance(step, PickCards)
    assert step.zones == (Zone.HAND, Zone.EQUIP)
    assert (step.lo, step.hi) == (1, 1)
    assert step.optional is False, "保留现状：这一步不给取消出口（票 07 §5 明写）"
    assert step.reveal_hand is False, "来源的手牌是隐藏槽（只有位置、没有牌面）"

    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="simayi")],
        deck=_deck_excluding(_cards("slash")[0]),
    )
    assert callable(step.face)
    assert step.face(state, _frame_with_point(0), 1, {}) == 0, "选取面读帧 = 伤害来源座位 0"
    assert step.face(state, _frame_with_point(0), 0, {}) == (), (
        "来源就是持有者自己 ⇒ 空元组（这一步不存在、一个询问都不发）"
    )
    assert step.actor is None, "应答者缺省 = 发动者 = 派发环传的 instance.owner"
    assert step.key == "card", "效果读答案按步骤名（不受逐点影响）"
    assert callable(step.cache_key), "帧内缓存键由派生函数给出"
    assert step.cache_key(_frame_with_point(0)) == "fankui:0"


# ---------------------------------------------------------------------------
# ⑤ 逐点触发：同一帧的两点伤害各问一次（缓存键带点号）
# ---------------------------------------------------------------------------


def test_fankui_step_key_separates_damage_points():
    """【反馈】选牌步的帧内缓存键**带点号**：同一次伤害的不同点不能共用一份缓存答案。

    迁移前的 ``ask_selection`` 键是 ``fankui:<来源>:<点号>``。若把步骤键写死成 ``"card"``
    或只带来源，同一次伤害事件的第 2 点会命中第 1 点的缓存答案 ⇒ **只搬一张牌**。
    这条直接钉键的形状（``PER_POINT`` 技能的逐点语义靠它；当前 B 族里 ``per_point=True``
    的是【遗计】，`per_point=False` 的【反馈】在真实派发里只跑第一点，故"两点各搬一张"
    的端到端读数不会出现——键本身的区分度才是真值源）。
    """
    assert fankui_step_key(_frame_with_point(0)) == "fankui:0"
    assert fankui_step_key(_frame_with_point(1)) == "fankui:1"


def test_fankui_runs_once_for_a_two_point_damage_event():
    """``per_point=False`` ⇒ 同一次伤害事件只问一次、只搬一张（票 05 的既有口径不被动到）。"""
    source_hand = (_cards("peach")[0], _cards("peach")[1])
    engine, simayi = _run_damage(source_hand=source_hand, amount=2)

    assert len(simayi.selections) == 1, "【反馈】不是逐点技：一次伤害事件只问一次"
    assert len(engine.state.player(1).hand) == 1, "只搬一张"
    assert len(engine.state.player(0).hand) == 1, "来源还剩一张"


# ---------------------------------------------------------------------------
# ⑥ PickCards.card_types（票 09）：牌名白名单过滤 + hi=None 与过滤后池同源
# ---------------------------------------------------------------------------


def test_pick_cards_card_types_filter_narrows_the_face_and_the_hi_none_limit():
    """``hi=None`` + ``card_types``：上限与选择面一起按**过滤后**的池算（票 09）。

    直接对执行器跑一步（最小宿主，只实现 :func:`run_flow` 用到的四个方法）：
    手牌 = 【杀】×2 + 【桃】×1，白名单 = ``("slash",)`` ⇒ 投影给前端的 ``total``
    上限是 **2**（不是手牌 3 张），选择面也只有那两张【杀】。
    """
    slashes = tuple(_cards("slash")[:2])
    peach = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(*slashes, peach)), SeatSpec()],
        deck=_deck_excluding(*slashes, peach),
        turn_seat=0,
    )
    seen: dict[str, object] = {}

    class _Host:
        def __init__(self) -> None:
            self.state = state

        @staticmethod
        def suspended(value) -> bool:
            return value == "SUSPEND"

        @staticmethod
        def accepted_selection(frame, *, owner, key):
            del frame, owner, key
            return None  # 夹具不模拟续跑：这一问还没答过

        @staticmethod
        def selection_face(seat, zones, *, reveal_hand):
            del reveal_hand
            slots, card_ids = [], []
            for zone in zones:
                for card_id in state.zone(ZoneRef(zone, seat)):
                    slots.append(Slot(ref=ZoneRef(zone, seat), card=state.card(card_id)))
                    card_ids.append(card_id)
            return tuple(slots), tuple(card_ids)

        @staticmethod
        def ask_selection(
            frame, *, owner, key, actor, timing, spec, build_face,
            revealed=(), ask_when_empty=False,
        ):
            del frame, owner, key, actor, timing
            slots, _card_ids = build_face()
            seen["total"] = spec.total
            seen["offered"] = [slot.card.card_id for slot in slots]
            return (0,)

    step = PickCards(
        zones=(Zone.HAND,),
        lo=1,
        hi=None,
        optional=False,
        card_types=("slash",),
    )

    _flow_step_cards(
        _Host(), _Frame(source=0), step, 0, {}, Timing.PHASE_PLAY_IDLE, ("probe", 0)
    )

    assert seen["total"] == (1, 2), "上限 = 过滤后的池大小（2 张【杀】），不是手牌 3 张"
    assert seen["offered"] == list(slashes), "选择面也只铺【杀】"


# ---------------------------------------------------------------------------
# ⑦ PickCards.slot_kinds（票 10）：槽位白名单过滤 + hi=None 与过滤后池同源
# ---------------------------------------------------------------------------


def test_pick_cards_slot_kinds_filter_narrows_the_face_and_the_hi_none_limit():
    """``hi=None`` + ``slot_kinds``：上限与选择面一起按**过滤后**的池算（票 10）。

    直接对执行器跑一步（与 ⑥ 同一个最小宿主）：装备区 = 武器【青釭剑】 + 防御马【绝影】，
    白名单 = 坐骑槽 ⇒ 投影给前端的 ``total`` 上限是 **1**（不是装备区 2 张），选择面
    也只有那匹坐骑 —— 【麒麟弓】"只铺目标的坐骑槽"正是这个形状。
    """
    weapon = _cards("qinggang_sword")[0]
    mount = _cards("jueying")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(weapon, mount)), SeatSpec()],
        deck=_deck_excluding(weapon, mount),
        turn_seat=0,
    )
    seen: dict[str, object] = {}

    class _Host:
        def __init__(self) -> None:
            self.state = state

        @staticmethod
        def suspended(value) -> bool:
            return value == "SUSPEND"

        @staticmethod
        def accepted_selection(frame, *, owner, key):
            del frame, owner, key
            return None  # 夹具不模拟续跑：这一问还没答过

        @staticmethod
        def selection_face(seat, zones, *, reveal_hand):
            del reveal_hand
            slots, card_ids = [], []
            for zone in zones:
                for card_id in state.zone(ZoneRef(zone, seat)):
                    slots.append(Slot(ref=ZoneRef(zone, seat), card=state.card(card_id)))
                    card_ids.append(card_id)
            return tuple(slots), tuple(card_ids)

        @staticmethod
        def ask_selection(
            frame, *, owner, key, actor, timing, spec, build_face,
            revealed=(), ask_when_empty=False,
        ):
            del frame, owner, key, actor, timing
            slots, _card_ids = build_face()
            seen["total"] = spec.total
            seen["offered"] = [slot.card.card_id for slot in slots]
            return (0,)

    step = PickCards(
        zones=(Zone.EQUIP,),
        lo=1,
        hi=None,
        optional=False,
        slot_kinds=(SlotKind.HORSE_DEFENSE, SlotKind.HORSE_OFFENSE),
    )

    _flow_step_cards(
        _Host(), _Frame(source=0), step, 0, {}, Timing.DAMAGE_DEAL, ("probe", 0)
    )

    assert seen["total"] == (1, 1), "上限 = 过滤后的池大小（1 匹坐骑），不是装备区 2 张"
    assert seen["offered"] == [mount], "选择面也只铺坐骑（武器不在其中）"


# ---------------------------------------------------------------------------
# ⑧ 评审 Standards finding 2：PickProvide 的面**只有一处缓存**（真引擎上读）
# ---------------------------------------------------------------------------


def test_provide_step_keeps_a_single_face_cache_on_the_real_engine():
    """提供步的面只缓存在 ``provide_faces[(询问位置, 询问名)]``，``Engine.ask_selection``
    不再另存一份。

    旧实现是两份：本步自己的帧内键与 ``Engine.ask_selection`` 内部的 ``frame.data["asks"]``。
    票 21 的 ``KeyError`` 正是两份不同源的结果。现在本步把面**直接交给** ``ask_selection``
    （``face=``）、后者不再往 ``asks`` 里写，故这里断的是结构事实：面在本步的键里、
    ``asks`` **根本不存在**；重入复用同一个面对象。票 22 起这个键与答案缓存**逐字同源**
    （``(询问位置, 询问名)``）而不是只认字符串 ``cache_key``，两个提供步共用 ``cache_key``
    也不会互相串面；同一步用可调用的 ``cache_key`` 逐轮改名时每轮各有一份面。
    """
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec()],
        deck=_deck_excluding(slash),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=3)
    frame = engine.push_frame(
        FrameKind.USE, {"source": 0, "targets": ()}, timing=Timing.RESOLVE_BEFORE_EFFECT
    )
    flow = SkillFlow(
        name="provide_probe",
        steps=(
            PickProvide(
                type_id="slash",
                zones=(Zone.HAND,),
                lo=1,
                hi=1,
                optional=False,
                key="card",
                cache_key="green_dragon_blade",
            ),
        ),
        effect=lambda host, frame, answers: None,
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay", "面铺好、发询问后挂起"
    face_key = (("provide_probe", 0), "green_dragon_blade")
    face = frame.data["provide_faces"][face_key]
    assert [slot.card.card_id for slot in face[0]] == [slash], "面 = 攻击者手里那张【杀】"
    assert "asks" not in frame.data, (
        "面只有一处缓存：ask_selection 收到现成 face 后不得再往 asks 里存第二份"
    )

    query = engine.pending
    assert query is not None and query.kind == "SELECT_CARDS"
    engine.submit(
        next(
            action
            for action in query.legal_actions
            if isinstance(action, SelectCards) and action.picks == (0,)
        )
    )
    run_flow(engine, frame, flow, owner=0)  # 重入：读回同一份面、命中答案缓存

    assert frame.data["provide_faces"][face_key] is face, "重入复用同一份面（不重算）"
    assert "asks" not in frame.data
    select_queries = [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SELECT_CARDS"
    ]
    assert len(select_queries) == 1, "重入不重发询问"


def test_a_provide_step_recomputes_its_face_when_the_ask_name_changes():
    """票 22：提供步的面与答案缓存**同名同源** —— 同一步换 ``cache_key`` 再问时要重算面。

    ``cache_key`` 的可调用档正是"同一步问多次"（逐点 / 逐轮）的机制（【反馈】的
    ``fankui_step_key``）。修复前提供步的面只按 ``ask_owner`` 缓存：第 2 轮复用第 1 轮的面，
    而答案却按 ``(ask_owner, cache_key)`` 取 ⇒ 两者再次不同源，正是票 22 要杀的那个病。
    本用例不改手牌，只钉结构事实：第 2 轮**重算了一份新面**（两个键各自留存、两次询问）。
    """
    slash_a = _cards("slash")[0]
    slash_b = _cards("slash")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash_a, slash_b)), SeatSpec()],
        deck=_deck_excluding(slash_a, slash_b),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=3)
    frame = engine.push_frame(
        FrameKind.USE, {"source": 0, "targets": ()}, timing=Timing.RESOLVE_BEFORE_EFFECT
    )
    flow = SkillFlow(
        name="provide_rounds",
        steps=(
            PickProvide(
                type_id="slash",
                zones=(Zone.HAND,),
                lo=1,
                hi=1,
                optional=False,
                key="card",
                cache_key=lambda frame_: f"round:{frame_.data.get('round', 0)}",
            ),
        ),
        effect=lambda host, frame, answers: None,
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay", "第 1 轮发问后挂起"
    first_key = (("provide_rounds", 0), "round:0")
    first = engine.pending
    assert first is not None
    engine.submit(
        next(
            action
            for action in first.legal_actions
            if isinstance(action, SelectCards) and action.picks == (0,)
        )
    )
    assert run_flow(engine, frame, flow, owner=0) is None, "第 1 轮提交（effect 是空操作）"
    face_round_0 = frame.data["provide_faces"][first_key]

    frame.data["round"] = 1
    assert run_flow(engine, frame, flow, owner=0) == "stay", "换询问名后必须再问一次"
    second_key = (("provide_rounds", 0), "round:1")
    face_round_1 = frame.data["provide_faces"][second_key]
    assert face_round_1 is not face_round_0, (
        "第 2 轮的面必须重算；修复前按 ask_owner 复用第 1 轮的面，答案与面不同源"
    )

    select_queries = [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SELECT_CARDS"
    ]
    assert len(select_queries) == 2, "两轮各问一次"


# ---------------------------------------------------------------------------
# ⑨ 票 22：同帧两个步骤共用 cache_key 也不得串答案（引擎按**询问位置**分桶）
# ---------------------------------------------------------------------------


def test_two_flow_steps_sharing_a_cache_name_ask_their_own_questions():
    """票 22 回归：同一帧里两个步骤即使共用 ``cache_key``，也各发各的询问、各拿各的答案。

    构造：步 1 铺**手牌**、步 2 铺**装备区**，两步的 ``cache_key`` 都是 ``"card"``
    （步骤 ``key`` 分别是 ``"hand"`` / ``"equip"``，故流程定义合法）。修复前
    ``ask_selection`` 的帧内答案只按 ``key`` 分桶，步 2 直接命中步 1 留下的答案 ⇒
    **不发第二个询问**，效果读到的是手牌里那张牌（与步 2 自己的面不同源）。
    """
    hand_card = _cards("peach")[0]
    equip_card = _cards("qinggang_sword")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(hand_card,), equip=(equip_card,)), SeatSpec()],
        deck=_deck_excluding(hand_card, equip_card),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=5)
    frame = engine.push_frame(
        FrameKind.USE, {"source": 0, "targets": ()}, timing=Timing.RESOLVE_BEFORE_EFFECT
    )
    captured: dict[str, object] = {}

    def effect(host, frame_, answers):
        captured.update(answers)
        return None

    flow = SkillFlow(
        name="cache_name_probe",
        steps=(
            PickCards(
                zones=(Zone.HAND,),
                lo=1,
                hi=1,
                optional=False,
                key="hand",
                cache_key="card",
            ),
            PickCards(
                zones=(Zone.EQUIP,),
                lo=1,
                hi=1,
                optional=False,
                key="equip",
                cache_key="card",
            ),
        ),
        effect=effect,
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay", "步 1 发问后挂起"
    first = engine.pending
    assert first is not None and first.kind == "SELECT_CARDS"
    engine.submit(
        next(
            action
            for action in first.legal_actions
            if isinstance(action, SelectCards) and action.picks == (0,)
        )
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay", "步 2 也必须发自己的询问"
    second = engine.pending
    assert second is not None and second.kind == "SELECT_CARDS", (
        "步 2 不得命中步 1 的答案缓存（修复前这里读回命令式答案、根本没有第二个询问）"
    )
    engine.submit(
        next(
            action
            for action in second.legal_actions
            if isinstance(action, SelectCards) and action.picks == (0,)
        )
    )
    assert run_flow(engine, frame, flow, owner=0) is None

    select_queries = [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SELECT_CARDS"
    ]
    assert len(select_queries) == 2, "两个步骤各发一次 SELECT_CARDS"
    assert captured["hand"] == (hand_card,)
    assert captured["equip"] == (equip_card,), "步 2 的答案必须来自步骤 2 自己的面（装备区）"


# ---------------------------------------------------------------------------
# ⑩ 票 22：流程定义在**构造期**就挡住"同流程内步骤重名"
# ---------------------------------------------------------------------------


def test_a_flow_rejects_two_steps_sharing_a_key():
    """同流程内步骤 ``key`` 两两不同（票 22）：重名会让 ``answers[step.key]`` 互相覆盖。

    询问缓存本身不会撞（位置含步骤序号），但 ``run_flow`` 按 ``step.key`` 回写答案，
    后者会盖掉前者、``effect`` 读到别步的答案 —— 这是流程定义写错，构造期就该炸。
    """
    with pytest.raises(ValueError, match="两两不同"):
        SkillFlow(
            name="dup_keys",
            steps=(
                PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="card"),
                PickProvide(type_id="slash", key="card"),
            ),
            effect=_noop,
        )


def test_a_flow_requires_a_non_empty_name():
    """``name`` 是询问位置的一部分（票 22），空名字会让两条流程的同一位置撞在一起。"""
    with pytest.raises(ValueError, match="name"):
        SkillFlow(
            name="",
            steps=(PickCards(zones=(Zone.HAND,), lo=1, hi=1),),
            effect=_noop,
        )


def test_a_flow_keeps_distinct_step_keys():
    """正例：键两两不同的两步流程照常构造（判据不能把合法形状一起挡掉）。"""
    flow = SkillFlow(
        name="distinct_keys",
        steps=(
            PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="card"),
            PickSeats(lo=1, hi=1, pool=lambda state, frame, owner, chosen: (), key="seat"),
        ),
        effect=_noop,
    )
    assert [step.key for step in flow.steps] == ["card", "seat"]


# ---------------------------------------------------------------------------
# ⑪ W4-1：预发动清单 = SkillFlow.steps 的渲染（顺序 / 区域 / 张数 / 候选池）
# ---------------------------------------------------------------------------


def _manifest_state(*, hand: tuple[int, ...], seats: int = 3):
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand)] + [SeatSpec() for _ in range(seats - 1)],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )


def _two_step_flow(pool):
    captured: dict[str, object] = {}

    def effect(host, frame, answers):
        captured.update(answers)
        return None

    flow = SkillFlow(
        name="manifest_probe",
        steps=(
            PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="card"),
            PickSeats(lo=1, hi=1, pool=pool, key="seat"),
        ),
        effect=effect,
    )
    return flow, captured


def test_manifest_renders_the_flow_steps_in_order():
    """清单字段 = ``{name, zones, min, max, pool, order}``，与步骤声明逐项同源。"""
    card = _cards("slash")[0]
    state = _manifest_state(hand=(card,))
    flow, _captured = _two_step_flow(
        lambda state, frame, owner, chosen: (1, 2)
    )

    steps = manifest(flow, state, 0)

    assert [type(step) for step in steps] == [Step, Step]
    assert [step.name for step in steps] == ["card", "seat"]
    assert [step.order for step in steps] == [0, 1]
    assert steps[0].zones == (Zone.HAND,)
    assert (steps[0].min, steps[0].max) == (1, 1)
    assert steps[0].pool == (StepPool(when=(), options=(str(card),)),)
    assert steps[1].zones == ()
    assert (steps[1].min, steps[1].max) == (1, 1)
    assert steps[1].pool == (StepPool(when=(), options=("seat:1", "seat:2")),)


def test_manifest_renders_a_per_option_sub_pool_once():
    """池依赖前序选择 ⇒ 为**每个前序选项**各下一行 ``when``（流离的武器 / 离间的使用者）。"""
    first, second = _cards("slash")[:2]
    state = _manifest_state(hand=(first, second))

    def pool(state, frame, owner, chosen):
        del state, owner, chosen
        # 池函数读**前序步骤答案**：这是清单与执行器共用的唯一通道。
        pick = int(frame.data["preactivation_answers"]["card"][0])
        return (1,) if pick == first else (2,)

    flow, _captured = _two_step_flow(pool)

    steps = manifest(flow, state, 0)

    assert steps[1].pool == (
        StepPool(when=(str(first),), options=("seat:1",)),
        StepPool(when=(str(second),), options=("seat:2",)),
    ), "逐选项子池：每个前序选择一行，when = 前序答案的规范文本"


def test_manifest_collapses_a_pool_that_does_not_depend_on_prior_steps():
    """所有前序组合给出同一份候选 ⇒ 收成一行 ``when=()``（不按前序选择重复 N 遍）。"""
    first, second = _cards("slash")[:2]
    state = _manifest_state(hand=(first, second))
    flow, _captured = _two_step_flow(
        lambda state, frame, owner, chosen: (1, 2)
    )

    steps = manifest(flow, state, 0)

    assert steps[1].pool == (StepPool(when=(), options=("seat:1", "seat:2")),)


def test_manifest_never_degrades_however_many_prior_options_there_are():
    """前序组合再多也**不退化**：逐选项各一行照发（用户裁定「永不退化」，spec §2 Q6）。

    改前这里钉的是相反的行为：组合数超过 ``MANIFEST_POOL_LIMIT = 32`` 就把该步的
    ``pool`` 整格抽掉，界面只能靠"全都点得动"兜底——票 11 删掉了那条路（同一步内"还能点谁"
    改由**步骤预览**端点现算），清单因此永远下发它算得出来的候选。

    证据取 33 张手牌（> 32）：每张各一行，行数与候选数一个不少。
    """
    hand = tuple(range(33))
    state = _manifest_state(hand=hand)

    def pool(state, frame, owner, chosen):
        del state, owner, chosen
        return (int(frame.data["preactivation_answers"]["card"][0]),)

    flow, _captured = _two_step_flow(pool)

    steps = manifest(flow, state, 0)

    assert len(hand) > 32, "夹具要**超过**旧上限，否则这条断言分不出退化与不退化"
    assert len(steps[1].pool) == len(hand), (
        f"每个前序选项一行：给了 {len(hand)} 张手牌，只见 {len(steps[1].pool)} 行"
    )
    assert [row.options for row in steps[1].pool] == [
        (f"seat:{card}",) for card in hand
    ], "每一行的候选 = 该前序答案下池回调给的真池"


def test_manifest_stops_at_the_first_step_answered_by_someone_else():
    """换应答者的第二段（反间猜花色 / 挑牌）**不进预发动**：清单只收本人应答的前缀。"""
    state = _manifest_state(hand=(_cards("slash")[0],))
    flow = SkillFlow(
        name="two_segments",
        steps=(
            PickSeats(lo=1, hi=1, pool=lambda state, frame, owner, chosen: (1,), key="seat"),
            PickCards(
                zones=(Zone.HAND,),
                lo=1,
                hi=1,
                key="card",
                actor=lambda state, frame, owner, answers: 1,
            ),
            PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="tail"),
        ),
        effect=_noop,
    )

    steps = manifest(flow, state, 0)

    assert [step.name for step in steps] == ["seat"], "第二段由目标应答，清单到此为止"


# ---------------------------------------------------------------------------
# ⑫ W4-1：复合动作校验器（区域 / 张数 / 目标池；首个失败原因）
# ---------------------------------------------------------------------------


def _validate_two_step(flow, state, picks, choices, *, owner=0):
    return validate_flow_activation(flow, state, owner, tuple(picks), tuple(choices))


def test_validate_activation_accepts_a_complete_composite_action():
    first = _cards("slash")[0]
    state = _manifest_state(hand=(first,))
    flow, _captured = _two_step_flow(
        lambda state, frame, owner, chosen: (1, 2)
    )

    assert _validate_two_step(flow, state, (first,), ("seat:1",)) is None
    assert _validate_two_step(flow, state, (first,), ("seat:2",)) is None


def test_validate_activation_rejects_the_first_reason_and_never_mutates_state():
    first = _cards("slash")[0]
    state = _manifest_state(hand=(first,))
    flow, _captured = _two_step_flow(
        lambda state, frame, owner, chosen: (1, 2)
    )
    before = state.model_dump()

    missing_card = _validate_two_step(flow, state, (), ("seat:1",))
    wrong_card = _validate_two_step(flow, state, (_cards("peach")[0],), ("seat:1",))
    wrong_seat = _validate_two_step(flow, state, (first,), ("seat:0",))
    duplicate = _validate_two_step(flow, state, (first, first), ("seat:1",))
    extra = _validate_two_step(flow, state, (first,), ("seat:1", "seat:2"))
    non_seat = _validate_two_step(flow, state, (first,), ("heart",))

    assert "至少需要 1 张牌" in missing_card
    assert "不在可选范围" in wrong_card
    assert "目标 0 不在可选范围" in wrong_seat
    assert "多余" in duplicate or "重复" in duplicate
    assert "多余" in extra
    assert "座位" in non_seat
    assert state.model_dump() == before, "校验失败必须零状态改动"


def test_validate_activation_enforces_the_exact_card_count():
    """恰好两张的成本步（结姻 / 贯石斧）：多一张少一张都拒绝。"""
    first, second, third = _cards("slash")[:3]
    state = _manifest_state(hand=(first, second, third))
    flow = SkillFlow(
        name="exactly_two",
        steps=(PickCards(zones=(Zone.HAND,), lo=2, hi=2, key="card"),),
        effect=_noop,
    )

    assert validate_flow_activation(flow, state, 0, (first, second), ()) is None
    assert "至少需要 2" in validate_flow_activation(flow, state, 0, (first,), ())
    assert "多余" in validate_flow_activation(flow, state, 0, (first, second, third), ())


# ---------------------------------------------------------------------------
# ⑬ W4-1：已校验的复合动作 → 逐步答案（run_flow 跳过本人应答的步骤）
# ---------------------------------------------------------------------------


class _NoAskHost:
    """只提供 ``run_flow`` 需要的最小面；任何发问都判失败（预填答案不该再问）。"""

    def __init__(self, state) -> None:
        self.state = state

    @staticmethod
    def suspended(value) -> bool:
        return value == "SUSPEND"

    @staticmethod
    def ask_selection(*args, **kwargs):  # pragma: no cover - 预填路径不该走到
        raise AssertionError("复合动作已带答案：本人应答的步骤不得再发问")

    @staticmethod
    def ask_option(*args, **kwargs):  # pragma: no cover - 预填路径不该走到
        raise AssertionError("复合动作已带答案：本人应答的步骤不得再发问")


def test_run_flow_consumes_the_composite_answers_without_asking():
    first = _cards("slash")[0]
    state = _manifest_state(hand=(first,))
    flow, captured = _two_step_flow(
        lambda state, frame, owner, chosen: (1,)
    )
    frame = _Frame(
        source=0,
        activation={"picks": (first,), "choices": ("seat:1",)},
    )

    assert run_flow(_NoAskHost(state), frame, flow, owner=0) is None

    assert captured == {"card": (first,), "seat": (1,)}, (
        "答案形状必须与逐步询问后 effect 读到的完全一致（卡牌 id 元组 / 座位元组）"
    )


def test_a_second_composite_payload_in_the_same_frame_does_not_replay_the_first():
    """同一帧里同一技能第二次发动是**另一份载荷**：缓存键带上载荷，不能重放旧 picks。

    W4-fix-6（D.2）：旧缓存的键只是 skill id。一张【杀】的 RESOLVE 帧可以先后开两次
    接缝（青龙 / 斧），第二次的 ``activation`` 若被第一次的缓存命中，摊回的答案就是
    第一次那张牌——效果会拿旧来源重算。本用例在同一帧上先后跑两份声明，断言第二次
    拿到的是**它自己**的答案。
    """
    first, second = _cards("slash")[:2]
    state = _manifest_state(hand=(first, second))
    flow, captured = _two_step_flow(lambda state, frame, owner, chosen: (1, 2))
    frame = _Frame(
        source=0,
        activation={"picks": (first,), "choices": ("seat:1",)},
    )

    assert run_flow(_NoAskHost(state), frame, flow, owner=0) is None
    assert captured == {"card": (first,), "seat": (1,)}

    frame.data["activation"] = {"picks": (second,), "choices": ("seat:2",)}
    assert run_flow(_NoAskHost(state), frame, flow, owner=0) is None

    assert captured == {"card": (second,), "seat": (2,)}, (
        "第二次发动必须摊回自己的声明，而不是命中第一次的缓存"
    )


# ---------------------------------------------------------------------------
# ⑭ W4-1：被动入口的清单随 SKILL_ACTIVATION 下发，复合动作照同一份声明执行
# ---------------------------------------------------------------------------


def test_a_passive_skill_executes_a_composite_action_from_its_manifest():
    """【麒麟弓】的 DAMAGE_DEAL 询问随附清单；提交复合动作后效果照常落地。"""
    bow = _cards("qilin_bow")[0]
    horse = _cards("jueying")[0]
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(bow,), hand=(slash,)),
            SeatSpec(equip=(horse,)),
        ],
        deck=_deck_excluding(slash, bow, horse),
        processing=(slash,),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    channel = state.card(slash)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 0,
            "targets": (1,),
            "card": channel,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 1,
            "channel": channel,
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )

    for _ in range(100):
        if engine.pending is not None:
            break
        engine._step()
    query = engine.pending
    assert query is not None and query.kind == QueryKind.SKILL_ACTIVATION
    assert query.skill_id == "equip.qilin_bow"
    assert [step.name for step in query.preactivation] == ["card"]
    assert query.preactivation[0].pool[0].options == (str(horse),), (
        "被动入口的清单也由 SkillFlow.steps 渲染：候选 = 目标装备区的坐骑"
    )

    engine.submit(
        ActivateSkill(
            skill_id="equip.qilin_bow",
            picks=(horse,),
            choices=(),
            answers_seq=query.seq,
        )
    )

    for _ in range(100):
        if horse not in engine.state.player(1).equip:
            break
        engine._step()

    assert horse not in engine.state.player(1).equip, "复合动作一次提交：坐骑被弃置"
    assert horse in engine.state.discard


def test_manifest_does_not_key_sub_pools_by_single_values_after_a_multi_select_step():
    """前序是多选步（结姻两张 / 仁德任意张）且池确实随它变 ⇒ 不下发候选索引。

    ``when`` 只能按单个选项取键，而前序答案是一组牌；逐单值行会与校验器读到的组合
    不同源（"界面给的行 ≠ 校验器接受的组合"）。此时退化为 ``pool=()``，由校验器裁决。
    """
    first, second, third = _cards("slash")[:3]
    state = _manifest_state(hand=(first, second, third))

    def pool(state, frame, owner, chosen):
        del state, owner, chosen
        picks = tuple(frame.data["preactivation_answers"]["cards"])
        return (1,) if len(picks) == 2 else (2,)

    captured: dict[str, object] = {}
    flow = SkillFlow(
        name="multi_select_probe",
        steps=(
            PickCards(zones=(Zone.HAND,), lo=2, hi=2, key="cards"),
            PickSeats(lo=1, hi=1, pool=pool, key="seat"),
        ),
        effect=lambda host, frame, answers: captured.update(answers),
    )

    steps = manifest(flow, state, 0)

    assert steps[1].pool == (), (
        "多选步之后不应按单个 card id 下发子池：那与校验器读到的组合不同源"
    )


# ---------------------------------------------------------------------------
# ⑮ W4-1：登记表与"已翻预发动"集合都由**声明**派生（不手抄 id 名单）
# ---------------------------------------------------------------------------


def test_the_preactivation_registry_and_the_flipped_set_are_derived_from_declarations():
    """``FLOWS`` / ``preactivated_skill_ids`` 读声明，不维护第二份名单。

    W4-1 不翻任何真实技能（``ACTIVE_SKILLS`` 里全为 ``False``），故这里用一份合成声明
    证明"翻过去"这条派生是活的；逐条技能由 W4-2 / W4-3 改声明，不改引擎。
    """
    declaration = ActiveSkill(
        "demo",
        "preactivated",
        lambda state, ctx, current: current,
        flow=_noop_flow(),
        preactivated=True,
    )

    flows = _build_flows(active=(declaration,), catalog={})
    assert flows["hero.demo.preactivated"][0][1] is declaration.flow
    assert preactivated_skill_ids(active=(declaration,), catalog={}) == frozenset(
        {"hero.demo.preactivated"}
    )


# ---------------------------------------------------------------------------
# ⑯ W6-02：``Arrange`` 步（排列不能用 pool 表达；固定切分在候选生成里收口）
# ---------------------------------------------------------------------------


def _noop_effect(host, frame, answers):
    """三参的**效果**替身（``FlowEffect`` 的形状）；``_noop`` 是钩子回调的两参形状。"""
    del host, frame, answers
    return None


def _arrange_flow(name: str, spec: DeckTopArrange) -> SkillFlow:
    """一条只含排列步的 test-only 流水线（不是李典：``CATALOG`` 里没有这种技能）。"""
    return SkillFlow(
        name=name,
        steps=(Arrange(key="cards", spec=spec),),
        effect=_noop_effect,
    )


def test_manifest_returns_no_steps_when_the_flow_contains_an_arrange_step():
    """含 ``Arrange`` 的流水线**整条**不下发清单：排列不是 ``Step.pool`` 能表达的形状。

    观星每帧问"是否发动"时 ``preactivation_for`` 都会走到这条路径（spec B2），故这里钉住
    "不炸"与"返回空元组"两件事：``Arrange`` 分支必须先于 ``_flow_actor``（后者会读
    ``step.actor``）。``Arrange`` 排在普通步骤**之后**也一样返回空 —— 排列之后的步骤依赖
    答案，不能把前缀清单单独下发。
    """
    card = _cards("slash")[0]
    state = _manifest_state(hand=(card,))

    assert manifest(
        _arrange_flow("arrange_manifest_probe", DeckTopArrange(count=2)), state, 0
    ) == ()

    prefixed = SkillFlow(
        name="arrange_manifest_prefix_probe",
        steps=(
            PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="card"),
            Arrange(key="cards", spec=DeckTopArrange(count=2)),
        ),
        effect=_noop,
    )
    assert manifest(prefixed, state, 0) == ()


def test_a_composite_activation_does_not_prefill_an_arrange_step():
    """排列的答案是 ``ArrangeCards``（顺序 + 切分），不是 picks / choices ⇒ 不能预填。

    空载荷不算"预填"（``_assign_activation`` 在 ``Arrange`` 处停下，执行器随后逐步问）；
    多带一个 pick 就是"用复合动作夹带排列载荷"，必须判错。
    """
    card = _cards("slash")[0]
    state = _manifest_state(hand=(card,))
    flow = _arrange_flow("arrange_activation_probe", DeckTopArrange(count=2))

    assert validate_flow_activation(flow, state, 0, (), ()) is None
    assert validate_flow_activation(flow, state, 0, (card,), ()) == (
        "复合动作里有多余的 picks（本技能的预发动清单没有用到）"
    )


def test_a_fixed_split_only_offers_the_declared_cut():
    """``free`` = 全部切分点（票 10 行为不变）；``fixed`` = 只铺声明的那个（spec S3）。"""
    state = _manifest_state(hand=(_cards("slash")[0],))

    free = legal_actions_for(
        state, 0, QueryKind.ARRANGE, answers_seq=7, payload=ArrangeContext(count=3)
    )
    assert {action.split for action in free} == {0, 1, 2, 3}
    assert len(free) == 6 * 4

    fixed = legal_actions_for(
        state,
        0,
        QueryKind.ARRANGE,
        answers_seq=7,
        payload=ArrangeContext(count=4, split=2),
    )
    assert {action.split for action in fixed} == {2}, "fixed：只有声明的切分点"
    assert {action.order for action in fixed} == set(permutations(range(4)))
    assert len(fixed) == 24, "4! 个排列 × 唯一的切分点"
    assert all(action.answers_seq == 7 for action in fixed)

    with pytest.raises(ValueError):
        legal_actions_for(
            state,
            0,
            QueryKind.ARRANGE,
            answers_seq=7,
            payload=ArrangeContext(count=2, split=3),
        )


def test_a_fixed_split_must_add_up_to_the_declared_count():
    """固定切分的两行必须**恰好**排完这次排列的牌（W6-07 / 评审 J6）。

    旧实现让 ``split_point`` 只回 ``top``，于是 ``FixedSplit(3, 2)`` 配 4 张牌会一路走到
    ``_arrange`` 的越界检查才炸（"固定切分点 3 不在 0..4 内"）—— 症状离错误来源很远。
    现在两个入口都先核对 ``top + bottom == count``：

    - 定数张数在**构造期**（声明写错了，import 期就该炸）；
    - 可调用张数在 ``split_point``（实际张数那时才知道，执行器在发问之前调它）。

    ``FixedSplit.bottom`` 因此是有读取方的字段，不是死配置。
    """
    with pytest.raises(ValueError, match="top=3 \\+ bottom=2"):
        DeckTopArrange(count=4, split=FixedSplit(3, 2))

    # 可调用张数读不到声明期 ⇒ 在 split_point 用**本次实际张数**核对。
    spec = DeckTopArrange(count=lambda _state, _frame: 4, split=FixedSplit(2, 2))
    assert spec.split_point(4) == 2
    with pytest.raises(ValueError, match="与张数 5 不一致"):
        spec.split_point(5)

    # free 档不受影响（没有声明切分，就没有可核对的半区）。
    assert DeckTopArrange(count=4).split_point(4) is None
    assert DeckTopArrange(count=4).split_point(3) is None


def test_a_synthetic_fixed_split_consumer_runs_the_arrange_step():
    """**test-only** 合成消费者（固定 2 张顶 / 2 张底）证明 ``DeckTopArrange`` 可复用。

    不是【李典】：这里不往 ``CATALOG`` 加任何技能定义，只在本用例里建一条流水线。钉四件事：
    首跑 ``peek_deck`` 拿牌面、``ARRANGE`` 只铺声明的切分点、答案形状 = ``ArrangeCards``、
    帧私有 peek 缓存把面内位置映射回实体牌（重入不重发询问）。
    """
    deck = _deck_excluding()
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=3)
    frame = engine.push_frame(
        FrameKind.USE, {"source": 0}, timing=Timing.RESOLVE_BEFORE_EFFECT
    )
    captured: dict[str, object] = {}

    def effect(host, frame, answers):
        captured.update(answers)
        return None

    flow = SkillFlow(
        name="fixed_split_probe",
        steps=(
            Arrange(
                key="cards",
                spec=DeckTopArrange(count=4, split=FixedSplit(2, 2)),
            ),
        ),
        effect=effect,
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay"
    query = engine.pending
    assert query is not None and query.kind == "ARRANGE"
    assert tuple(card.card_id for card in query.revealed) == deck[:4], "看牌堆顶 count 张"
    assert {action.split for action in query.legal_actions} == {2}
    assert len(query.legal_actions) == 24

    engine.submit(
        next(action for action in query.legal_actions if action.order == (3, 2, 1, 0))
    )
    assert run_flow(engine, frame, flow, owner=0) is None

    answer = captured["cards"]
    assert isinstance(answer, ArrangeCards), "答案形状 = ArrangeCards 本身（spec S12）"
    assert (answer.order, answer.split) == ((3, 2, 1, 0), 2)
    assert tuple(card.card_id for card in watched_cards(frame, flow, "cards")) == deck[:4], (
        "位置 → 实体牌读帧私有 peek 缓存"
    )
    assert (
        len(
            [
                record
                for record in engine.records
                if record.kind == "query" and record.query.kind == "ARRANGE"
            ]
        )
        == 1
    ), "重入命中 ask_arrange 的帧内缓存，不重发询问"


# ---------------------------------------------------------------------------
# ⑧ PickOption.revealed（票 01 自制包）：选项步把观看授予透传给引擎
# ---------------------------------------------------------------------------


def test_pick_option_forwards_the_revealed_grant():
    """``PickOption.revealed`` 原样传给 ``Engine.ask_option``（票 01，【义起】的观看+三选一）。

    观看授予是"先看到这几张牌，再点一项"的**同一问**：执行器若不透传，弹窗里就没有牌面，
    玩家只能盲选类型。定值档与可调用档各验一次（用户裁定的观看张数是**现算**的，
    故可调用档才是真路径）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
    )
    watched = (state.card(state.deck[0]), state.card(state.deck[1]))
    seen: dict[str, object] = {}

    class _Host:
        def __init__(self) -> None:
            self.state = state

        @staticmethod
        def suspended(value) -> bool:
            return value == "SUSPEND"

        @staticmethod
        def accepted_option(frame, *, skill_id, actor):
            del frame, skill_id, actor
            return None  # 夹具不模拟续跑：这一问还没答过

        @staticmethod
        def ask_option(
            frame, actor, timing, *, skill_id, options, optional=False, revealed=(),
        ):
            del frame, actor, timing, skill_id, options, optional
            seen["revealed"] = tuple(card.card_id for card in revealed)
            return _Choice()

    class _Choice:
        choice = "a"

    host = _Host()
    fixed = PickOption(options=("a", "b"), optional=False, key="type", revealed=watched)
    _flow_step_option(host, _Frame(source=0), fixed, 0, {}, Timing.PHASE_PLAY_IDLE, ("probe", 0))
    assert seen["revealed"] == tuple(card.card_id for card in watched)

    dynamic = PickOption(
        options=("a", "b"),
        optional=False,
        key="type",
        revealed=lambda _state, _frame, _owner, _answers: watched,
    )
    _flow_step_option(host, _Frame(source=0), dynamic, 0, {}, Timing.PHASE_PLAY_IDLE, ("probe", 1))
    assert seen["revealed"] == tuple(card.card_id for card in watched), "可调用档也现算透传"


# ---------------------------------------------------------------------------
# ⑨ PickOption.disabled（票 04 §3）：选项**显示但不可选**
# ---------------------------------------------------------------------------


def _option_flow(*, options, disabled=()):
    """一条"选一项"的探针流水线（票 04 §3 的置灰通道：清单 + 校验器各读一份）。"""
    captured: dict[str, object] = {}

    def effect(host, frame, answers):
        captured.update(answers)
        return None

    flow = SkillFlow(
        name="option_probe",
        steps=(
            PickOption(options=options, disabled=disabled, optional=False, key="name"),
        ),
        effect=effect,
    )
    return flow, captured


def test_manifest_renders_the_disabled_options_of_a_row():
    """``PickOption.disabled`` 进 ``StepPool.disabled``：选项**在候选里**但不可选。

    置灰与"不在候选里"是两件事：界要把名字**列出来**（"此刻没有合法目标"是一条提示），
    而合法性仍由候选集合回答。故 ``options`` 恒为全量闭集，``disabled`` 只多带一份"这些
    点不动"。读取方 = 前端 ``preactivation.js`` 的选项按钮（见 ui_smoke 的合成载荷）。
    """
    state = _manifest_state(hand=(_cards("slash")[0],))
    flow, _captured = _option_flow(options=("a", "b", "c"), disabled=("b",))

    steps = manifest(flow, state, 0)

    assert steps[0].pool == (
        StepPool(when=(), options=("a", "b", "c"), disabled=("b",)),
    )


def test_manifest_keeps_the_full_option_set_when_every_option_is_disabled():
    """全被置灰也**不**退化成"没有清单"：清单照样下发，界面照旧列出名字（都是灰的）。"""
    state = _manifest_state(hand=(_cards("slash")[0],))
    flow, _captured = _option_flow(options=("a", "b"), disabled=("a", "b"))

    steps = manifest(flow, state, 0)

    assert steps[0].pool == (
        StepPool(when=(), options=("a", "b"), disabled=("a", "b")),
    )


def test_the_disabled_payload_is_omitted_when_the_row_has_none():
    """没有置灰项 ⇒ ``disabled`` **不进序列化**（与 ``StepPool.max`` 同一姿态）。

    清单里绝大多数行都没有置灰项，恒发一个空数组会改掉既有载荷的字节形状。
    """
    step = Step(name="name", min=1, max=1, order=0, pool=(StepPool(options=("a",)),))

    row = step.model_dump(mode="json")["pool"][0]

    assert "disabled" not in row
    grayed = Step(
        name="name",
        min=1,
        max=1,
        order=0,
        pool=(StepPool(options=("a",), disabled=("b",)),),
    ).model_dump(mode="json")["pool"][0]
    assert grayed["disabled"] == ["b"]


def test_validate_activation_rejects_a_disabled_option():
    """置灰选项**不许靠界面挡**：校验器 fail-closed，与候选同源。

    复合动作是"提交即接受"的通道（不看 ``legal_actions``），故"点不动"必须在这里也成立——
    否则改一个前端状态就能提交一个"此刻用不了"的声明。
    """
    state = _manifest_state(hand=(_cards("slash")[0],))
    flow, captured = _option_flow(options=("a", "b"), disabled=("b",))

    error = validate_flow_activation(flow, state, 0, (), ("b",))

    assert error is not None and "b" in error, f"置灰选项必须被拒（首个原因）：{error}"
    assert captured == {}, "被拒的动作一个副作用都不产生"
    assert validate_flow_activation(flow, state, 0, (), ("a",)) is None


def test_the_disabled_set_is_recomputed_from_the_prior_answers():
    """可调用档按**前序答案**现算（与 ``options`` 同形态）：蛊惑的牌名步就是这一档。

    同一张牌名闭集在不同来源牌下"能不能用"不同（产物继承来源牌的花色 / 点数 ⇒ 目标池
    会变），故置灰项必须与候选**同一条链路**：清单渲染、校验、界面三处读同一份解析结果。
    """
    card = _cards("slash")[0]

    def disabled(state, frame, owner, answers):
        del state, frame, owner
        return ("b",) if answers["prior"] == (card,) else ()

    def options(state, frame, owner, answers):
        del state, frame, owner
        return ("a", "b")

    flow = SkillFlow(
        name="option_probe_dynamic",
        steps=(
            PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="prior"),
            PickOption(
                options=options,
                disabled=disabled,
                optional=False,
                key="name",
            ),
        ),
        effect=_noop,
    )
    state = _manifest_state(hand=(card,))

    steps = manifest(flow, state, 0)

    assert [(row.options, row.disabled) for row in steps[1].pool] == [
        (("a", "b"), ("b",))
    ], "置灰项按前序答案（那张来源牌）现算"


def test_the_step_by_step_ask_never_offers_a_disabled_option():
    """逐步询问那条路也要认置灰（票 04 §3）：``ask_option`` 的候选 = ``options`` − ``disabled``。

    置灰在**三处**收口：清单画灰、复合动作被校验器拒收、以及这里（未翻预发动的流水线 /
    被动技的逐步问）。少了第三条，玩家会在真正的询问里看到并选中一个"点了必被拒"的选项。
    """
    asked: dict[str, object] = {}

    class _Choice:
        choice = "a"

    class _Host:
        state = None

        @staticmethod
        def suspended(value) -> bool:
            return value == "SUSPEND"

        @staticmethod
        def accepted_option(frame, *, skill_id, actor):
            del frame, skill_id, actor
            return None  # 夹具不模拟续跑：这一问还没答过

        @staticmethod
        def ask_option(frame, actor, timing, *, skill_id, options, optional=False, revealed=()):
            del frame, actor, timing, skill_id, optional, revealed
            asked["options"] = tuple(options)
            return _Choice()

    _Host.state = _manifest_state(hand=(_cards("slash")[0],))
    step = PickOption(
        options=("a", "b", "c"), disabled=("b",), optional=False, key="name"
    )

    status, value = _flow_step_option(
        _Host(), _Frame(source=0), step, 0, {}, Timing.PHASE_PLAY_IDLE, ("probe", 0)
    )

    assert status == "ok" and value == "a"
    assert asked["options"] == ("a", "c"), "置灰的那一项不下发给询问"


def test_a_disabled_option_can_read_the_frame_during_validation():
    """校验期的置灰回调拿得到**真帧**（票 04 §3；原先这一支把 GameState 当帧传）。

    可调用档的签名是 ``(state, frame, owner, answers)``，而 `_assign_activation` 的选项步
    曾经把 ``state`` 当帧塞进 ``_with_answers``——回调在那里读 ``frame.data`` 会拿到空字典。
    这里让置灰回调**硬读**一个只有真帧才有的键：读不到就 KeyError，正好红。
    """
    card = _cards("slash")[0]
    state = _manifest_state(hand=(card,))

    def disabled(state, frame, owner, answers):
        del state, owner, answers
        return ("b",) if frame.data.get("probe_marker") else ()

    flow = SkillFlow(
        name="option_probe_frame",
        steps=(
            PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="card"),
            PickOption(options=("a", "b"), disabled=disabled, optional=False, key="name"),
        ),
        effect=_noop,
    )

    class _RealFrame:
        """只有 ``data`` 的真帧替身：`_with_answers` 会把它原样透传下去。"""

        def __init__(self) -> None:
            self.data = {"source": 0, "probe_marker": True}

    error = validate_flow_activation(
        flow, state, 0, (card,), ("b",), frame=_RealFrame()
    )

    assert error is not None and "b" in error, (
        f"置灰回调读到了真帧里的标记 ⇒ 'b' 必须被判不可选，实得 {error!r}"
    )


# ---------------------------------------------------------------------------
# ⑮ 续跑：先认**已接受的答案**，再算池（本 effort 票 01）
#
# 形状 = 实机 bug（【攻心】"置于牌堆顶"）：effect 已经改过步骤池要读的那份状态
# （hand→DECK 的移动触发了会提问的【连营】⇒ 挂起），续跑时**按活状态现算**的池已经空了
# ⇒ 步骤 `"cancel"` ⇒ `run_flow` 按"零副作用"返回 None ⇒ effect 不再重入、尾巴丢失。
# ---------------------------------------------------------------------------


def _resume_engine(*, target_hand: tuple[int, ...]):
    """座位 0 = 发动者，座位 1 = 目标（``target_hand`` 是它的全部手牌）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hand=target_hand)],
        deck=tuple(
            card_id
            for card_id in range(len(PACK.deck.cards))
            if card_id not in set(target_hand)
        ),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=3)
    frame = engine.push_frame(
        FrameKind.SKILL,
        {"source": 0, "targets": (), "skill_id": "hero.probe.resume"},
        timing=Timing.PHASE_PLAY_IDLE,
    )
    return engine, frame


def _answer_pending(engine, predicate, what: str) -> None:
    """把当前挂起的询问按 ``predicate`` 挑一条候选答掉。"""
    query = engine.pending
    assert query is not None, f"期望『{what}』的询问挂起，实际没有挂起的询问"
    action = next((item for item in query.legal_actions if predicate(item)), None)
    assert action is not None, f"『{what}』的候选里没有期望的答案：{query.legal_actions}"
    engine.submit(action)


def _move_target_last_card(engine) -> int:
    """把目标（座位 1）唯一那张手牌搬进牌堆（**追加到牌堆底**，故只有置顶那一行能救它）。

    这是实机 bug 的因果本身：effect 先 ``move_in_frame(..., to_zone=DECK)``，随后才
    ``place_on_deck(to_top=True)``；移动那一行挂起 ⇒ 续跑时目标手牌已空。
    """
    card_id = engine.state.player(1).hand[0]
    move_card(
        engine.state,
        EffectContext(actor=0),
        (engine.state.card(card_id),),
        to_zone=ZoneRef(Zone.DECK),
        reason=MoveReason.PUT,
        owner=1,
    )
    return card_id


def test_a_resumed_seat_step_uses_the_accepted_answer_instead_of_recomputing_the_pool():
    """座位步：effect 改过池读的状态后，续跑必须采用已接受的座位、重入 effect。

    时序复刻实机 bug（【攻心】）：**第 1 次** ``run_flow`` 发问挂起 → 应答 → **第 2 次**
    续跑（引擎本来就会重入）采用那条应答、跑 effect、effect 里的移动又挂起（这里用
    ``return "stay"`` 表示）→ 目标最后一张手牌在挂起期间被搬走 → **第 3 次**续跑。

    修复前第 3 次按活状态重算 pool ⇒ 空池 ⇒ ``"cancel"`` ⇒ ``run_flow`` 返回 ``None``
    （"零副作用"）⇒ effect **不再重入**：只被调用一次、池被读了两次（第 2 次读到 0 张）。
    断言因此是"effect 进了两次、池只读到 1 张各一次"，不是"返回值不是 cancel"——取消与
    走完都返回 ``None``，只看返回值会被骗过去。
    """
    target = _cards("peach")[0]
    engine, frame = _resume_engine(target_hand=(target,))
    pool_reads: list[int] = []
    captured: list[dict] = []

    def pool(state, frame_, owner, chosen):
        del frame_, owner, chosen
        pool_reads.append(len(state.player(1).hand))
        return (1,) if state.player(1).hand else ()

    def effect(host, frame_, answers):
        captured.append(dict(answers))
        # 第一次跑就挂起（真实形状：effect 里的 hand→DECK 移动让出了控制权）。
        return "stay" if len(captured) == 1 else None

    flow = SkillFlow(
        name="seat_resume",
        steps=(PickSeats(lo=1, hi=1, pool=pool, optional=False, key="seat"),),
        effect=effect,
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay", "座位步发问后挂起"
    _answer_pending(
        engine, lambda action: getattr(action, "choice", None) == "seat:1", "选座位"
    )
    assert run_flow(engine, frame, flow, owner=0) == "stay", "effect 跑了一次就挂起"
    _move_target_last_card(engine)
    assert engine.state.player(1).hand == []

    assert run_flow(engine, frame, flow, owner=0) is None
    assert captured == [{"seat": (1,)}, {"seat": (1,)}], (
        "续跑必须采用**已接受**的座位答案并**重入** effect；"
        f"实际 effect 调用 = {captured}（只有一条 = 整条流被当成 cancel 了）"
    )
    assert pool_reads == [1, 1], (
        "续跑不再按活状态重算池：池只在应答落地前算过（两次都在目标还有手牌时）；"
        f"实际每次读到的目标手牌数 = {pool_reads}"
    )


def test_a_resumed_card_step_uses_the_accepted_cards_instead_of_re_filtering_the_pool():
    """选牌步 + 白名单：过滤后的池在续跑时为空也不许把整条流判成 cancel。

    ``hi=None``（"全部可选牌"）+ ``card_types`` / ``slot_kinds`` / ``exclude_cards`` 让执行器
    在**发问之前**就按活状态算一次"过滤后还够不够 ``lo``"（同样是现算池的一种；写死 ``hi``
    时这条短路不生效，故 ``hi=None`` 是必需的夹具成分）。
    """
    slash = _cards("slash")[0]
    engine, frame = _resume_engine(target_hand=(slash,))
    captured: list[dict] = []

    def effect(host, frame_, answers):
        captured.append(dict(answers))
        return "stay" if len(captured) == 1 else None

    flow = SkillFlow(
        name="card_resume",
        steps=(
            PickCards(
                zones=(Zone.HAND,),
                lo=1,
                hi=None,
                card_types=("slash",),
                optional=False,
                key="cost",
                face=lambda state, frame_, owner, answers: 1,
            ),
        ),
        effect=effect,
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay", "选牌步发问后挂起"
    _answer_pending(
        engine, lambda action: isinstance(action, SelectCards) and action.picks == (0,), "选牌"
    )
    assert run_flow(engine, frame, flow, owner=0) == "stay", "effect 跑了一次就挂起"
    _move_target_last_card(engine)

    assert run_flow(engine, frame, flow, owner=0) is None
    assert captured == [{"cost": (slash,)}, {"cost": (slash,)}], (
        "续跑必须采用**已接受**的牌并**重入** effect；"
        f"实际 effect 调用 = {captured}（只有一条 = 过滤后的池空了就被判 cancel）"
    )


def test_a_resumed_option_step_uses_the_accepted_option_instead_of_re_resolving_it():
    """选项步：可调用档 ``options`` 现算的闭集在续跑时为空也不许取消整条流。

    界【结姻】那类"可选哪几项取决于前序答案 / 目标状态"的选项步走的就是这条现算路径；
    effect 一旦改过那份状态，续跑现算就会得到空闭集。
    """
    peach = _cards("peach")[0]
    engine, frame = _resume_engine(target_hand=(peach,))
    captured: list[dict] = []

    def options(state, frame_, owner, answers):
        del frame_, owner, answers
        return ("keep", "give") if state.player(1).hand else ()

    def effect(host, frame_, answers):
        captured.append(dict(answers))
        return "stay" if len(captured) == 1 else None

    flow = SkillFlow(
        name="option_resume",
        steps=(
            PickOption(options=options, optional=False, key="mode"),
        ),
        effect=effect,
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay", "选项步发问后挂起"
    _answer_pending(
        engine, lambda action: getattr(action, "choice", None) == "give", "选一项"
    )
    assert run_flow(engine, frame, flow, owner=0) == "stay", "effect 跑了一次就挂起"
    _move_target_last_card(engine)

    assert run_flow(engine, frame, flow, owner=0) is None
    assert captured == [{"mode": "give"}, {"mode": "give"}], (
        "续跑必须采用**已接受**的选项并**重入** effect；"
        f"实际 effect 调用 = {captured}（只有一条 = 现算的选项集空了就被判 cancel）"
    )


def test_a_cancel_after_the_effect_started_is_a_hard_error():
    """``"cancel"`` 的语义 = "零副作用、技能没发生"；effect 已经开始之后**不许**再走它。

    这不是可达的正常路径（上面的三步都先认已接受的答案）——它是一条**契约兜底**：若某一步
    的询问标识在挂起期间变了（这里显式把 ``cache_key`` 从 ``round:0`` 改到 ``round:1``），
    池又已经空了，实现只能**显式失败**，不能把"已经改过状态的发动"报成"取消、可再发动"。
    见 `CODING_STANDARDS.md` §3（挂起幂等）与本 effort 的 spec「范围」第 2 条。
    """
    slash = _cards("slash")[0]
    engine, frame = _resume_engine(target_hand=(slash,))
    frame.data["round"] = 0
    effect_runs: list[str] = []

    def effect(host, frame_, answers):
        effect_runs.append("run")
        # 第一次跑就挂起（真实形状：effect 里的移动让出了控制权）。
        return "stay" if len(effect_runs) == 1 else None

    flow = SkillFlow(
        name="guard_resume",
        steps=(
            PickCards(
                zones=(Zone.HAND,),
                lo=1,
                hi=1,
                optional=False,
                key="cost",
                cache_key=lambda frame_: f"round:{frame_.data['round']}",
                face=lambda state, frame_, owner, answers: 1,
            ),
        ),
        effect=effect,
    )

    assert run_flow(engine, frame, flow, owner=0) == "stay", "选牌步发问后挂起"
    _answer_pending(
        engine, lambda action: isinstance(action, SelectCards) and action.picks == (0,), "选牌"
    )
    assert run_flow(engine, frame, flow, owner=0) == "stay", "effect 跑了一次就挂起"

    _move_target_last_card(engine)
    frame.data["round"] = 1  # 询问名变了 ⇒ 已接受的那份答案不再是"这一问"的

    with pytest.raises(RuntimeError, match="cancel"):
        run_flow(engine, frame, flow, owner=0)
    assert len(effect_runs) == 1, "显式失败：不再重入 effect（effect 也没有第二次副作用）"


def test_an_empty_pool_on_the_first_run_still_cancels_with_zero_side_effects():
    """真·空池（第一次执行时就没有候选）仍走 ``"cancel"`` 且**真的零副作用**（§6 边界）。

    修复不能把"取消"这条正常出口一并收掉：池从一开始就空 ⇒ 不发没有候选的询问、
    effect 不跑、什么都不改。
    """
    engine, frame = _resume_engine(target_hand=())
    captured: list[dict] = []

    def effect(host, frame_, answers):
        captured.append(dict(answers))
        return None

    flow = SkillFlow(
        name="empty_pool",
        steps=(
            PickSeats(
                lo=1,
                hi=1,
                pool=lambda state, f, owner, chosen: (1,) if state.player(1).hand else (),
                optional=False,
                key="seat",
            ),
        ),
        effect=effect,
    )

    assert run_flow(engine, frame, flow, owner=0) is None, "池从一开始就空 ⇒ 取消"
    assert captured == [], "取消 = effect 不跑"
    assert engine.pending is None, "不发没有候选的询问"
    assert not [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_OPTION
    ], "空池不发询问（记录流里一条 SELECT_OPTION 都没有）"
