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

import pytest

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.skills import Hook, Seam, catalog as catalog_module
from sgs.skills.damage_after import FANKUI_FLOW, fankui_step_key
from sgs.skills.flow import PickCards, SkillFlow, _flow_step_cards
from sgs.state import (
    FrameKind,
    Pass,
    QueryKind,
    SelectCards,
    SelectCardsQuery,
    Slot,
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
    return SkillFlow(steps=(PickCards(zones=(Zone.HAND,), lo=1, hi=1),), effect=_noop)


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
        def selection_face(seat, zones, *, reveal_hand):
            del reveal_hand
            slots, card_ids = [], []
            for zone in zones:
                for card_id in state.zone(ZoneRef(zone, seat)):
                    slots.append(Slot(ref=ZoneRef(zone, seat), card=state.card(card_id)))
                    card_ids.append(card_id)
            return tuple(slots), tuple(card_ids)

        @staticmethod
        def ask_selection(frame, *, key, actor, timing, spec, build_face):
            del frame, key, actor, timing
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

    _flow_step_cards(_Host(), _Frame(source=0), step, 0, {}, Timing.PHASE_PLAY_IDLE)

    assert seen["total"] == (1, 2), "上限 = 过滤后的池大小（2 张【杀】），不是手牌 3 张"
    assert seen["offered"] == list(slashes), "选择面也只铺【杀】"
