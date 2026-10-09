"""多人濒死回退（票 31 / 决策回票 20）：同一 ``NEAR_DEATH`` 帧内的窗口回退。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流。四条**机械断言**
（票 20 §5）：

1. 帧内 ``NEAR_DEATH_ENTER`` ≡ 1、``NEAR_DEATH_AFTER`` ≡ 1、``NEAR_DEATH_AT`` 条数 = 窗口数；
2. 第 2 条 ``NEAR_DEATH_AT`` 之后的首条 ``query.actor`` = 触发回复的那条 ``action.actor``（= B）；
3. 窗口 n 的应答者序列是窗口 n−1 的**座位后缀**（"先前问过的人不再问"）；
4. 每窗询问数 ≤ 存活人数。

构造：M2 唯一 >1 点伤害源 = 标准 108 的【闪电】（3 点**无来源**雷电伤害）落在 1~2 体力
角色上，使体力可达 −2（票 20 §6）；濒死窗口只能出【桃】，故体力在窗口内单调上升、
窗口数有界（**不新增守卫**，终止性是结构不变量）。【闪电】由**被判定者本人**的判定阶段
结算，故濒死者恒 = 当前回合角色、也是首窗的起点。

场景（票 20 §6）：**R1 同一起点回退**、**R3 起点前进**（两条必备）、**R2 体力 −2**
（可选的下限用例），外加一条**区分后缀与整圈重算**的三窗用例（断言 3 的判别面）。
基本 53 张档单次伤害恒 1 ⇒ M1 六条冻结基准绝不可达回退（由 ``test_replays`` 的轨迹摘要
另行兜底），本文件不重复。
"""

from __future__ import annotations

from sgs.data import ContentPack, Suit, load_content_pack
from sgs.engine import Engine
from sgs.state import EndPhase, FrameKind, Pass, Phase, Query, UseCard
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_turns

STANDARD = "standard_108"
PACK = load_content_pack(deck_profile=STANDARD)
PEACH = cards_of_type(PACK, "peach")
LIGHTNING = cards_of_type(PACK, "lightning")


def _spade_in_lightning_range(pack: ContentPack) -> int:
    """【闪电】命中所需的一张 ♠2~9 判定牌；判定结果由测试显式指定，不依赖随机。"""
    for index, card in enumerate(pack.deck.cards):
        if card.suit is Suit.SPADE and card.rank is not None and 2 <= card.rank <= 9:
            return index
    raise AssertionError("标准 108 张里找不到 ♠2~9")


SPADE_IN_RANGE = _spade_in_lightning_range(PACK)


class _PeachPlanPlayer:
    """确定性脚本玩家：按 ``peaches`` 预算在求桃窗口出【桃】，其余询问走最小合法应答。"""

    def __init__(self, seat: int, *, peaches: int = 0) -> None:
        self.seat = seat
        self.peaches = peaches

    def respond(self, query: Query):
        if query.kind == "PEACH":
            if self.peaches > 0:
                peach = _find(query, UseCard, type_id="peach")
                if peach is not None:
                    self.peaches -= 1
                    return peach
            return _find(query, Pass)
        if query.kind == "PLAY_PHASE":
            end = _find_or_none(query, EndPhase)
            if end is not None:
                return end
        return query.legal_actions[0]


def _find(query: Query, action_type, *, type_id: str | None = None):
    found = _find_or_none(query, action_type, type_id=type_id)
    if found is None:
        raise AssertionError(f"{query.kind} 询问里没有 {action_type.__name__} 候选")
    return found


def _find_or_none(query: Query, action_type, *, type_id: str | None = None):
    for action in query.legal_actions:
        if not isinstance(action, action_type):
            continue
        if type_id is not None and action.card.type != type_id:
            continue
        return action
    return None


def _lightning_engine(*, hp: int, peaches: dict[int, int], players: int = 8) -> Engine:
    """座位 0 判定区一张【闪电】、牌堆顶一张 ♠2~9；各座位按 ``peaches`` 备桃。

    ``peaches[seat]`` = 该座位在整个濒死结算里的出桃预算（每次被问都用一张，直到用完）。
    """
    seats = [
        SeatSpec(
            hp=hp,
            hp_max=4,
            judge=(LIGHTNING[0],),
            hand=tuple(PEACH[: peaches.get(0, 0)]),
        )
    ]
    for seat in range(1, players):
        count = peaches.get(seat, 0)
        offset = 2 + (seat - 1) * 2
        seats.append(SeatSpec(hp=4, hand=tuple(PEACH[offset : offset + count])))
    used = {LIGHTNING[0], SPADE_IN_RANGE} | {
        card_id for spec in seats for card_id in spec.hand
    }
    deck = (SPADE_IN_RANGE,) + tuple(
        card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used
    )
    state = build_state(
        pack=PACK, seats=seats, deck=deck, turn_seat=0, phase=Phase.PREPARE
    )
    engine = Engine.from_state(state, seed=1)
    run_turns(
        engine,
        [_PeachPlanPlayer(seat, peaches=peaches.get(seat, 0)) for seat in range(players)],
        turns=1,
    )
    return engine


# ---------------------------------------------------------------------------
# 记录流读取助手
# ---------------------------------------------------------------------------


def _timings(records, *, frame_id: int) -> list[str]:
    return [
        record.timing.value
        for record in records
        if record.kind == "timing" and record.frame_id == frame_id
    ]


def _near_death_frame_id(records) -> int:
    """唯一 ``NEAR_DEATH`` 帧的 id（本文件的构造局面都只经历一次濒死结算）。"""
    frames = [
        record
        for record in records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.NEAR_DEATH
    ]
    assert len(frames) == 1, "每个构造局面只压一个 NEAR_DEATH 帧"
    return frames[0].frame_id


def _window_actors(records, *, frame_id: int) -> list[list[int]]:
    """按 ``NEAR_DEATH_AT`` 记录切分，给出每窗被询问者的座位序列。"""
    windows: list[list[int]] = []
    current: list[int] | None = None
    for record in records:
        if record.kind == "timing" and record.frame_id == frame_id:
            if record.timing.value == Timing.NEAR_DEATH_AT.value:
                current = []
                windows.append(current)
            elif record.timing.value == Timing.NEAR_DEATH_AFTER.value:
                current = None
        elif record.kind == "query" and current is not None and record.query.kind == "PEACH":
            # ``query`` 记录不带 ``frame_id``，但濒死帧在栈上的整段里只有求桃会发
            # ``PEACH`` 询问，故"落在两条窗口边界之间"就等价于"属于本帧本窗"。
            current.append(record.query.actor)
    return windows


def _hp_changes(records, *, seat: int) -> list[tuple[int, int]]:
    return [
        (record.before, record.after)
        for record in records
        if record.kind == "delta"
        and record.delta_type == "hp_change"
        and record.seat == seat
    ]


def _rollback_trigger_actor(records, *, frame_id: int) -> int:
    """触发回退的那名响应者：机械断言 2 的"触发回复的 ``action.actor``"。

    触发的那条应答 = 第 2 条 ``NEAR_DEATH_AT`` 之前**最近一次**被接受的使用【桃】；
    应答它的 ``PEACH`` 询问的 ``actor`` 就是响应者 B。``query`` / ``action`` 记录不带
    ``frame_id``，但第 2 条 AT 之前的记录必属本帧首窗，故按序回溯即可定位。
    """
    at_positions = [
        index
        for index, record in enumerate(records)
        if record.kind == "timing"
        and record.frame_id == frame_id
        and record.timing.value == Timing.NEAR_DEATH_AT.value
    ]
    assert len(at_positions) >= 2, "没有第 2 条 NEAR_DEATH_AT ⇒ 本局没有发生回退"
    for action_index in range(at_positions[1] - 1, -1, -1):
        action = records[action_index]
        if not (action.kind == "action" and action.accepted):
            continue
        if not isinstance(action.action, UseCard) or action.action.card.type != "peach":
            continue
        for query_index in range(action_index - 1, -1, -1):
            query = records[query_index]
            if query.kind == "query" and query.query.kind == "PEACH":
                return query.query.actor
    raise AssertionError("找不到触发回退的那次【桃】响应")


# ---------------------------------------------------------------------------
# R1：同一起点回退（必备）
# ---------------------------------------------------------------------------


def test_a_peach_that_leaves_hp_below_one_rolls_back_to_the_same_start():
    """hp 2 → 闪电 −1；同一应答者两张桃 ⇒ 1 次回退 / 2 窗口，全程仍是一个濒死帧。

    见票 20 §6 的 R1。四条机械断言逐条核对：ENTER ≡ 1 / AFTER ≡ 1 / AT 条数 = 窗口数 /
    第 2 窗首条 ``query.actor`` = 触发回复者。
    """
    engine = _lightning_engine(hp=2, peaches={0: 2})
    frame_id = _near_death_frame_id(engine.records)
    timings = _timings(engine.records, frame_id=frame_id)

    assert timings.count(Timing.NEAR_DEATH_ENTER.value) == 1, "进入濒死全程恰一次"
    assert timings.count(Timing.NEAR_DEATH_AFTER.value) == 1, "结束濒死全程恰一次"
    assert timings.count(Timing.NEAR_DEATH_AT.value) == 2, "体力仍 < 1 ⇒ 时机重发、开第二窗"

    windows = _window_actors(engine.records, frame_id=frame_id)
    assert windows == [[0], [0]], "起点 = 刚响应完的座位（此处即首窗起点）"
    assert windows[1][0] == _rollback_trigger_actor(engine.records, frame_id=frame_id), (
        "第 2 窗首条 query.actor = 触发回复的那条 action 的 actor（机械断言 2）"
    )

    assert _hp_changes(engine.records, seat=0) == [(2, -1), (-1, 0), (0, 1)], (
        "3 点雷电伤害后由两张【桃】逐点回满 1"
    )
    assert Timing.NEAR_DEATH_AT.value in timings, "被救回 ⇒ 不进入死亡结算"


# ---------------------------------------------------------------------------
# R3：起点前进到下一个响应者（必备）
# ---------------------------------------------------------------------------


def test_the_new_window_starts_at_the_responder_and_skips_seats_already_asked():
    """hp 2 → 闪电 −1；座位 0 放弃、座位 1 出一桃（仍 < 1）⇒ 第 2 窗从座位 1 起。

    座位 0 在首窗已被问过，故**不再被问**——这是"新区间 = 旧区间的后缀"的可观测面。
    随后座位 2 的第二张桃把体力补到 1，窗口收尾。见票 20 §6 的 R3。
    """
    engine = _lightning_engine(hp=2, peaches={1: 1, 2: 1})
    frame_id = _near_death_frame_id(engine.records)
    timings = _timings(engine.records, frame_id=frame_id)

    assert timings.count(Timing.NEAR_DEATH_AT.value) == 2
    assert timings.count(Timing.NEAR_DEATH_ENTER.value) == 1
    assert timings.count(Timing.NEAR_DEATH_AFTER.value) == 1

    windows = _window_actors(engine.records, frame_id=frame_id)
    assert windows[0] == [0, 1], "首窗：起点座位 0 放弃后才轮到座位 1"
    assert windows[1] == [1, 2], "回退窗是首窗的后缀：从座位 1 起，座位 0 不再被问"
    trigger = _rollback_trigger_actor(engine.records, frame_id=frame_id)
    assert windows[1][0] == trigger == 1, "第 2 窗首条 query.actor = 触发回复的座位 1"

    # 机械断言"窗口 n 是 n−1 的座位后缀"：8 人全存活、起点 = 当前回合角色 0，故首窗的
    # 完整应答者区间是座位环 ``0..7``；回退窗 = 该元组从座位 1（触发回复者）起的后缀。
    assert windows[1] == list(range(8))[1:][: len(windows[1])]

    assert _hp_changes(engine.records, seat=0) == [(2, -1), (-1, 0), (0, 1)]


def test_a_third_window_keeps_the_suffix_endpoint_and_never_wraps_to_asked_seats():
    """hp 1 → 闪电 −2；座位 0、2 各出一桃 ⇒ 第 3 窗只含 ``(2..7)``，绝不绕回 0 / 1。

    这是能**区分**"后缀"与"从响应者起的整圈重算"的用例：整圈重算会得到
    ``(2,3,4,5,6,7,0,1)``，而座位 0 / 1 在更早的窗口里已被问过。终点仍是当前回合
    角色（座位 0）的上家（座位 7）——**继承不重算**（票 20 §3）。
    """
    engine = _lightning_engine(hp=1, peaches={0: 1, 2: 1})
    frame_id = _near_death_frame_id(engine.records)
    timings = _timings(engine.records, frame_id=frame_id)
    windows = _window_actors(engine.records, frame_id=frame_id)

    assert timings.count(Timing.NEAR_DEATH_AT.value) == 3, "两次严格回复 ⇒ 三个窗口"
    assert windows == [[0], [0, 1, 2], [2, 3, 4, 5, 6, 7]], (
        "第 3 窗 = 第 2 窗从座位 2 起的后缀，终点仍为座位 7"
    )
    assert 0 not in windows[2] and 1 not in windows[2], "先前问过的人不再被问"
    assert _hp_changes(engine.records, seat=0) == [(1, -2), (-2, -1), (-1, 0)]

    deaths = [
        record
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.DEATH
    ]
    assert len(deaths) == 1, "第 3 窗无人继续出桃 ⇒ 体力停在 0、照常进入死亡结算"


# ---------------------------------------------------------------------------
# R2：可达下限 hp = −2（可选的下限用例）
# ---------------------------------------------------------------------------


def test_three_windows_are_reachable_when_lightning_drops_hp_to_minus_two():
    """hp 1 → 闪电 −2；三张桃逐点回到 1 ⇒ 2 次回退 / 3 窗口（M2 可达下限）。"""
    engine = _lightning_engine(hp=1, peaches={0: 3})
    frame_id = _near_death_frame_id(engine.records)
    timings = _timings(engine.records, frame_id=frame_id)

    assert timings.count(Timing.NEAR_DEATH_AT.value) == 3, "体力 −2 ⇒ 需要三个窗口"
    assert timings.count(Timing.NEAR_DEATH_ENTER.value) == 1
    assert timings.count(Timing.NEAR_DEATH_AFTER.value) == 1

    windows = _window_actors(engine.records, frame_id=frame_id)
    assert windows == [[0], [0], [0]], "每窗都从同一响应者（座位 0）回退"

    assert _hp_changes(engine.records, seat=0) == [(1, -2), (-2, -1), (-1, 0), (0, 1)]


def test_all_pass_keeps_a_single_window_and_enters_death():
    """没人出桃（含"出了牌但体力没升"的边界不在 M2）：单窗之后照常进入死亡结算。

    回退的前提是**严格回复过体力**；全员放弃不满足，故 ``NEAR_DEATH_AT`` 只有一条，
    ``NEAR_DEATH_AFTER`` 之后压入 ``DEATH``——回退没有把无人救的路径改掉。
    """
    engine = _lightning_engine(hp=1, peaches={})
    frame_id = _near_death_frame_id(engine.records)
    timings = _timings(engine.records, frame_id=frame_id)

    assert timings.count(Timing.NEAR_DEATH_AT.value) == 1
    assert timings.count(Timing.NEAR_DEATH_ENTER.value) == 1
    assert timings.count(Timing.NEAR_DEATH_AFTER.value) == 1

    windows = _window_actors(engine.records, frame_id=frame_id)
    assert windows == [list(range(8))], "每名存活者各被问一次（≤ 存活人数）"

    deaths = [
        record
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.DEATH
    ]
    assert len(deaths) == 1, "无人救 ⇒ 死亡结算照常插入"
