"""区域坐标化与记录面 v2（施工票 21）：六个区、装备槽位、判定区读写、归属。

落点两处，与票 21 的"测试缝"一致：

- **低层入口**（主缝的低层，先例 ``tests/support/positions.py``）：手工构造 ``GameState``
  后直接调 :func:`~sgs.effects.move_card`，断言区域坐标、槽位兜底与归属；
- **库门面**（主缝）：``Engine.new_game`` 产出的记录流里，开局发牌与洗牌的坐标形状。

不测帧私有数据、不测 ``pc``；断言落在**记录流**与**公开状态**上。
"""

from __future__ import annotations

from typing import Mapping

import pytest

from sgs.data import ContentPack, RuleConfig, load_content_pack, validate_content_pack
from sgs.effects import EffectContext, locate, move_card
from sgs.engine import Engine, instantiate_cards
from sgs.state import (
    CardMoveDelta,
    MoveReason,
    SlotKind,
    Zone,
    ZoneRef,
)

from support import SeatSpec, build_state

PACK = load_content_pack()


# ---------------------------------------------------------------------------
# 六个区与区域坐标（票 08 §1）
# ---------------------------------------------------------------------------


def test_the_zone_enum_is_exactly_the_six_m2_zones():
    assert {zone.value for zone in Zone} == {
        "deck",
        "hand",
        "discard",
        "processing",
        "equip",
        "judge",
    }


def test_move_reasons_are_exactly_the_six_m2_values():
    assert {reason.value for reason in MoveReason} == {
        "DRAW",
        "USE",
        "DISCARD",
        "PUT",
        "JUDGE",
        "LOSE",
    }


def test_a_zone_coordinate_serialises_the_seat_explicitly_including_null():
    """票 16 §9：``seat`` 显式写出（含 ``null``），形状不随序列化选项漂移。"""
    delta = CardMoveDelta(
        cards=(),
        from_zone=ZoneRef(Zone.HAND, 1),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=1,
    )

    dumped = delta.model_dump(mode="json")

    assert dumped["from_zone"] == {"zone": "hand", "seat": 1}
    assert dumped["to_zone"] == {"zone": "discard", "seat": None}
    assert CardMoveDelta.model_validate(dumped) == delta, "坐标值可往返反序列化"


def test_zone_refs_are_frozen_values():
    assert ZoneRef(Zone.JUDGE, 0) == ZoneRef(Zone.JUDGE, 0)
    assert ZoneRef(Zone.JUDGE, 0) != ZoneRef(Zone.JUDGE, 1)
    assert ZoneRef(Zone.DECK) != ZoneRef(Zone.PROCESSING)


def test_per_seat_zones_are_reachable_only_with_a_seat():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])

    assert state.zone(ZoneRef(Zone.DECK)) is state.deck
    assert state.zone(ZoneRef(Zone.HAND, 1)) is state.player(1).hand
    assert state.zone(ZoneRef(Zone.EQUIP, 0)) is state.player(0).equip
    assert state.zone(ZoneRef(Zone.JUDGE, 0)) is state.player(0).judge

    with pytest.raises(ValueError, match="座位"):
        state.zone(ZoneRef(Zone.HAND))
    with pytest.raises(ValueError, match="座位"):
        state.zone(ZoneRef(Zone.EQUIP))


def test_locate_reports_the_zone_coordinate_of_a_card():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(0,), judge=(1,)), SeatSpec(equip=(2,))],
    )

    assert locate(state, 0) == ZoneRef(Zone.HAND, 0)
    assert locate(state, 1) == ZoneRef(Zone.JUDGE, 0)
    assert locate(state, 2) == ZoneRef(Zone.EQUIP, 1)

    with pytest.raises(ValueError, match="牌守恒"):
        locate(state, 52)


# ---------------------------------------------------------------------------
# 开局记录流里的区域坐标与归属（票 16 §3 的 body 变化）
# ---------------------------------------------------------------------------


def test_the_opening_shuffle_and_deal_use_zone_coordinates_and_belonging():
    engine = Engine.new_game(seed=7, rule_config=RuleConfig(), pack=PACK)
    shuffle = engine.records[1]
    deals = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DRAW
    ]

    assert shuffle.from_zone is None and shuffle.to_zone == ZoneRef(Zone.DECK)
    assert [deal.to_zone for deal in deals] == [ZoneRef(Zone.HAND, 0), ZoneRef(Zone.HAND, 1)]
    assert [deal.from_zone for deal in deals] == [ZoneRef(Zone.DECK)] * 2
    assert [deal.owner for deal in deals] == [0, 1], "摸牌归属 = 摸牌者"


# ---------------------------------------------------------------------------
# 装备区：槽位是牌面事实 + 撞槽兜底 + 槽序（票 08 §2 / 票 12 §Q2）
# ---------------------------------------------------------------------------


def _equipment_pack() -> ContentPack:
    """一份最小装备内容包：两张武器（同槽）+ 一张防具（另一槽）+ 一张宝物（留位不启用）。"""
    raw: Mapping[str, object] = {
        "schema_version": 1,
        "pack_id": "test-equip",
        "deck_profile": "tiny",
        "card_types": [
            {"id": "blade", "name": "测试刀", "category": "equipment", "subcategory": "weapon"},
            {"id": "shield", "name": "测试盾", "category": "equipment", "subcategory": "armor"},
            {"id": "relic", "name": "测试宝", "category": "equipment", "subcategory": "treasure"},
        ],
        "decks": {
            "tiny": {
                "cards": [
                    {"type": "blade", "suit": "spade", "rank": 5},
                    {"type": "blade", "suit": "spade", "rank": 6},
                    {"type": "shield", "suit": "heart", "rank": 2},
                    {"type": "relic", "suit": "club", "rank": 3},
                ]
            }
        },
    }
    return validate_content_pack(raw, file="test-equip.json")


def test_the_equipment_slots_are_the_five_of_the_decision_with_treasure_reserved():
    assert {slot.value for slot in SlotKind} == {
        "weapon",
        "armor",
        "horse_defense",
        "horse_offense",
        "treasure",
    }


def test_the_basic_53_cards_carry_no_equipment_slot():
    assert {card.slot for card in instantiate_cards(PACK)} == {None}, (
        "basic_53 没有装备牌，槽位一律为 None（扩表不得污染旧表）"
    )


def test_the_slot_is_a_card_face_fact_derived_from_the_subcategory():
    refs = instantiate_cards(_equipment_pack())

    assert [ref.slot for ref in refs] == [
        SlotKind.WEAPON,
        SlotKind.WEAPON,
        SlotKind.ARMOR,
        None,
    ], "槽位在实例化时一次派生；宝物槽位预留不启用（ADR-006）"


def test_putting_a_card_into_an_occupied_equipment_slot_is_a_programming_error():
    """一般"置入"撞已占槽位即抛兜底；替换必须由调用方先移出旧牌（票 08 §3 / 票 12 §Q3）。"""
    pack = _equipment_pack()
    state = build_state(pack=pack, seats=[SeatSpec(hand=(0, 1, 2)), SeatSpec()])
    ctx = EffectContext(actor=0)

    first = move_card(
        state,
        ctx,
        state.refs((0,)),
        to_zone=ZoneRef(Zone.EQUIP, 0),
        reason=MoveReason.PUT,
        owner=0,
    )
    assert first.from_zone == ZoneRef(Zone.HAND, 0)
    assert first.to_zone == ZoneRef(Zone.EQUIP, 0)

    with pytest.raises(ValueError, match="装备槽位"):
        move_card(
            state,
            ctx,
            state.refs((1,)),
            to_zone=ZoneRef(Zone.EQUIP, 0),
            reason=MoveReason.PUT,
            owner=0,
        )

    assert state.player(0).equip == [0], "被拒的置入零状态改动"


def test_equipment_cards_are_kept_in_slot_enum_order():
    pack = _equipment_pack()
    state = build_state(pack=pack, seats=[SeatSpec(hand=(0, 2)), SeatSpec()])
    ctx = EffectContext(actor=0)

    # 先放防具、再放武器：存储序仍是槽位枚举序（武器在防具前）。
    for card_id in (2, 0):
        move_card(
            state,
            ctx,
            state.refs((card_id,)),
            to_zone=ZoneRef(Zone.EQUIP, 0),
            reason=MoveReason.PUT,
            owner=0,
        )

    assert state.player(0).equip == [0, 2]
    assert [state.card(card_id).slot for card_id in state.player(0).equip] == [
        SlotKind.WEAPON,
        SlotKind.ARMOR,
    ]


# ---------------------------------------------------------------------------
# 判定区：先入在前、读取端 reversed 表后进先出；判定移动带被判定者
# ---------------------------------------------------------------------------


def test_the_judge_zone_stores_first_in_first_and_reads_last_in_first_out():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(0, 1)), SeatSpec()],
        deck=tuple(range(2, len(PACK.deck.cards))),
    )
    ctx = EffectContext(actor=0)

    for card_id in (0, 1):
        move_card(
            state,
            ctx,
            state.refs((card_id,)),
            to_zone=ZoneRef(Zone.JUDGE, 0),
            reason=MoveReason.PUT,
            owner=0,
        )

    assert state.player(0).judge == [0, 1], "存储序 = 先入在前"
    assert list(reversed(state.player(0).judge)) == [1, 0], (
        "结算序 = 最后置入先取（读取端 reversed）；存储序与结算序不得混为一谈"
    )


def test_the_two_judgment_moves_belong_to_the_judged_seat():
    """判定牌 ``DECK → PROCESSING → DISCARD`` 两条移动都带被判定者（票 16 §7）。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], deck=(0,))
    ctx = EffectContext(actor=1)
    card = state.refs((0,))

    up = move_card(
        state, ctx, card, to_zone=ZoneRef(Zone.PROCESSING), reason=MoveReason.JUDGE, owner=1
    )
    assert (up.from_zone, up.to_zone, up.reason, up.owner) == (
        ZoneRef(Zone.DECK),
        ZoneRef(Zone.PROCESSING),
        MoveReason.JUDGE,
        1,
    )

    down = move_card(
        state, ctx, card, to_zone=ZoneRef(Zone.DISCARD), reason=MoveReason.JUDGE, owner=1
    )
    assert (down.from_zone, down.to_zone, down.reason, down.owner) == (
        ZoneRef(Zone.PROCESSING),
        ZoneRef(Zone.DISCARD),
        MoveReason.JUDGE,
        1,
    )


def test_a_teardown_move_without_a_character_subject_has_no_belonging():
    """终局收尾扫处理区：无角色主体 ⇒ ``owner = None``（票 16 §7 的唯一例外情形）。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], processing=(0,))

    delta = move_card(
        state,
        EffectContext(actor=0),
        state.refs((0,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.USE,
        owner=None,
    )

    assert delta.owner is None
