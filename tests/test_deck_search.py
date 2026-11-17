"""按牌名从牌堆取一张：``Engine.take_named_card_from_deck``（框架票 03；骆统【勤政】）。

口径（用户 2026-11-14 对票 01 的 Q3 / Q4 签署）：

- **均匀随机**（Q4 = A）：在**全部同名牌**里均匀抽一张——用专用子流
  ``DECK_SEARCH_SUBSTREAM``，观星 / 五谷之后玩家不能靠已知牌堆顶把想要的牌钉给自己；
- **牌堆无同名牌 ⇒ 该次不发生**（Q3 = A）：返回 ``None``、不落记录、不消耗随机位
  （不洗牌堆、不取弃牌堆、不判平局）；
- **不改既有子流的位置**：与"不调用该入口"的对照局相比，``shuffle`` / ``select`` 的下一次
  取数逐值相同（新子流是名字哈希派生 + 惰取，一个随机位都不从别处借）。

取数路径（``CODING_STANDARDS.md`` §6）：抽签走真引擎入口；对照局走**同一份牌堆 / 弃牌堆
输入**，只差"有没有调用过该入口"这一件事。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import FrameKind
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, deck_excluding

PACK = load_content_pack(deck_profile="standard_108")
SLASH = "杀"
"""【勤政】3 的倍数那一档的第一个牌名（另外还有【闪】/【桃】/【酒】等档位，与框架无关）。"""


def _engine(*, deck=None, seed: int = 11) -> tuple[Engine, object]:
    """人为局面 + 真引擎：两人局、牌堆 = 标准 108 的全序（可裁剪）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK) if deck is None else deck,
        turn_seat=0,
    )
    return Engine.from_state(state, seed=seed), state


def _take(seed: int, *, names=frozenset({SLASH})) -> int | None:
    """同一局面、同一 seed 下取一张；返回 ``card_id``（``None`` = 没有同名牌）。"""
    engine, _ = _engine(seed=seed)
    card = engine.take_named_card_from_deck(None, names=names, to_seat=0)
    return None if card is None else card.card_id


def _peek_frame(engine: Engine):
    """一个只为 ``peek_deck`` 落 ``deck_shuffle`` 记录而存在的 MOVE 帧（不推进它）。"""
    return engine.push_frame(
        FrameKind.MOVE,
        {"source": 0, "targets": (), "card": None},
        timing=Timing.PHASE_PLAY_IDLE,
    )


def _next_shuffle_order(engine: Engine, feeder: tuple[int, ...]) -> tuple[int, ...]:
    """把牌堆清空、弃牌堆置成同一份输入，然后看牌堆顶 ⇒ **必然触发一次重洗**。

    返回值 = 重洗之后的整副牌堆顺序：它完全由 ``shuffle`` 子流的下一次取数决定，故两个
    引擎的这个值相同 ⇔ 那条子流的位置没有被别的东西挪动过。
    """
    engine.state.deck.clear()
    engine.state.discard[:] = list(feeder)
    engine.peek_deck(_peek_frame(engine), 1)
    return tuple(engine.state.deck)


# ---------------------------------------------------------------------------
# 均匀随机 + 可复现 + 只消耗自己的子流
# ---------------------------------------------------------------------------


def test_take_named_card_is_uniform_and_consumes_only_its_own_substream():
    # ① 可复现：同一 seed 的两次连续取牌，两条引擎给出同一对结果。
    first, _ = _engine(seed=29)
    second, _ = _engine(seed=29)
    pair = [
        first.take_named_card_from_deck(None, names={SLASH}, to_seat=0).card_id,
        first.take_named_card_from_deck(None, names={SLASH}, to_seat=0).card_id,
    ]
    again = [
        second.take_named_card_from_deck(None, names={SLASH}, to_seat=0).card_id,
        second.take_named_card_from_deck(None, names={SLASH}, to_seat=0).card_id,
    ]
    assert pair == again, "同一 seed 的同一串取牌必须逐值可复现"

    # ② 均匀（Q4=A）：换 seed 抽出来的不是同一张，也不是"牌堆序第一张"。64 个种子里
    #    取得 ≥10 个不同结果；恒取牌堆序第一张时这里恒为 1。
    picks = [_take(seed) for seed in range(64)]
    assert len(set(picks)) >= 10, (
        f"64 个种子只取到 {len(set(picks))} 种结果：这不叫「在全部同名牌里均匀随机」"
    )

    # ③ 抽到的确实是**牌堆里**的同名牌，且真的进了手牌（走 move_in_frame 那条口）。
    engine, state = _engine(seed=29)
    slash_ids = set(cards_of_type(PACK, "slash"))
    card = engine.take_named_card_from_deck(None, names={SLASH}, to_seat=0)
    assert card is not None and card.card_id in slash_ids, "结果必须是【杀】"
    assert state.player(0).hand == [card.card_id], "取到的牌落在目标座位的手牌区"
    moves = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_move"
    ]
    assert [(m.from_zone.zone.value, m.to_zone.zone.value) for m in moves] == [
        ("deck", "hand")
    ], "落一条 card_move（就地 move_card 会让记录流重建的牌堆与活状态分叉）"

    # ④ 只消耗自己的子流：与"不调用该入口"的对照局相比，shuffle / select 的下一次取数相同。
    base_deck = tuple(engine.state.deck)  # 取走之后的牌堆（对照局照这个牌堆建）
    assert engine._deck_search is not None, "取过牌 ⇒ 子流已经派生（懒取后的正常态）"

    control, _ = _engine(deck=base_deck, seed=29)
    assert control.state.deck == list(base_deck), "对照局：同一牌堆、只是没人取走过一张"

    feeder = tuple(base_deck[:20])  # 同一份重洗输入（两张牌堆都已清空）
    assert _next_shuffle_order(engine, feeder) == _next_shuffle_order(control, feeder), (
        "取过牌的那一局重洗顺序被改写了 ⇒ deck_search 动到了 shuffle 子流的位置"
    )

    order_a, order_b = list(range(8)), list(range(8))
    engine.shuffle_positions(order_a)
    control.shuffle_positions(order_b)
    assert order_a == order_b, "select 子流（隐藏槽乱序）的位置也被动过"


# ---------------------------------------------------------------------------
# 无候选 ⇒ 返回 None、不落记录、不消耗随机位
# ---------------------------------------------------------------------------


def test_take_named_card_returns_none_and_records_nothing_when_no_name_matches():
    engine, state = _engine(seed=7)
    records_before = engine.records
    hand_before = list(state.player(0).hand)
    deck_before = list(state.deck)

    assert engine.take_named_card_from_deck(None, names={"牌堆里没有这个牌名"}, to_seat=0) is None

    assert engine.records == records_before, "不落记录（连 timing / 空 delta 都没有）"
    assert len(engine.records) == 1, "前置：这条局面只有 header 一条记录"
    assert state.player(0).hand == hand_before, "手牌不动"
    assert state.deck == deck_before, "牌堆不动（不洗牌堆、不取弃牌堆 —— Q3=A）"
    assert engine._deck_search is None, "连子流都没派生 ⇒ 一个随机位都没取"

    # "不消耗随机位"的实测：空转之后第一次取牌，与从未空转过的对照局逐值相同。
    assert engine.take_named_card_from_deck(None, names={SLASH}, to_seat=0).card_id == _take(7)

    # 牌堆空 ⇒ 同样是无候选，不是"洗弃牌堆再找"（Q3=A 的另一半）。
    empty, _ = _engine(deck=(), seed=7)
    assert empty.take_named_card_from_deck(None, names={SLASH}, to_seat=0) is None
    assert empty.state.discard == [], "Q3=A 的另一半：没有「把弃牌堆洗进牌堆再找」这种兜底"
