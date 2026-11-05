"""「获得牌」/「失去牌」的唯一定义（M6 票 24；用户裁定 Q1–Q5）。

两个词以前各住在一位技能的条件里（清俭一处、连营 / 枭姬 / 白银狮子三处），判据各写各的。
本文件把它们收成 :func:`~sgs.skills.move_after.gains_card` /
:func:`~sgs.skills.move_after.loses_card` 两个函数，并用**一张效果登记表**钉住分类：

=========================  ========  ========  ======================================
效果                        获得?     失去?     依据
=========================  ========  ========  ======================================
摸牌（牌堆 → 手牌）           ✓         —       用户裁定 Q1：摸牌**算**获得
重铸的摸一张                  ✓         ✓        Q1（摸）+ 重铸本身是失去
五谷丰登（处理区 → 手牌）      ✓         —        处理区 → 手牌是获得
顺手牵羊 / 反馈               ✓         ✓        获得方进手牌；失去方离开手牌 / 装备区
交给别人（我手牌 → 他手牌）    —         ✓        **Q2：交给别人算失去手牌**
置入装备区（手牌 → 装备区）   —         ✓        Q3：获得只指进手牌；失去看离开的区域
谦逊扣牌（手牌 → 武将牌上）    —         ✓        离开手牌区
武将牌上 → 手牌              ✓         —        进手牌区
武将牌上 → 弃牌堆             —         —        Q5：不算失去
判定牌（处理区 → 弃牌堆）     —         —        Q4：不算失去
判定区（我判定区 → 弃牌堆）   —         —        Q4：不算失去
=========================  ========  ========  ======================================

判据只有两句：**获得 = 进我的手牌区**（来源不限，牌堆也算 —— Q1）；
**失去 = 从我的手牌区或装备区离开**（去向不限，交给别人也算 —— Q2；判定区 / 武将牌上的牌
不算 —— Q4 / Q5）。登记表是这两句的**逐效果样本**，不是第三份实现。
"""

from __future__ import annotations

import pytest

from sgs.skills.move_after import MoveEvent, gains_card, loses_card
from sgs.state import MoveReason, Zone, ZoneRef

ME = 0
OTHER = 1

Move = tuple[tuple[Zone, int | None], tuple[Zone, int | None]]


def _move(from_zone: tuple[Zone, int | None], to_zone: tuple[Zone, int | None]) -> MoveEvent:
    """造一次移动的载荷（两个判据只读区域与座位，不读牌面）。"""
    return MoveEvent(
        cards=(),
        from_zone=ZoneRef(from_zone[0], from_zone[1]),
        to_zone=ZoneRef(to_zone[0], to_zone[1]),
        reason=MoveReason.LOSE,
        owner=ME,
        after_holder=to_zone[1],
        actor=ME,
    )


#: 说明 → (来源区, 去向区, 对 ME 算获得?, 对 ME 算失去?)
CASES: dict[str, tuple[Move, bool, bool]] = {
    # Q1（票 25 落地）：摸牌 / 无中生有 / 重铸的摸一张都算**获得**（来源不限）。
    "摸牌阶段摸牌": ((Zone.DECK, None), (Zone.HAND, ME), True, False),
    "无中生有 / 重铸的摸一张": ((Zone.DECK, None), (Zone.HAND, ME), True, False),
    "五谷丰登（处理区拿牌）": ((Zone.PROCESSING, None), (Zone.HAND, ME), True, False),
    "顺手牵羊（拿走别人的手牌）": ((Zone.HAND, OTHER), (Zone.HAND, ME), True, False),
    "别人顺手牵羊拿走我的牌": ((Zone.HAND, ME), (Zone.HAND, OTHER), False, True),
    "交给别人（仁德 / 清俭给牌）": ((Zone.HAND, ME), (Zone.HAND, OTHER), False, True),
    "使用 / 打出（进处理区）": ((Zone.HAND, ME), (Zone.PROCESSING, None), False, True),
    "弃牌阶段弃牌": ((Zone.HAND, ME), (Zone.DISCARD, None), False, True),
    "过河拆桥（弃置我的手牌）": ((Zone.HAND, ME), (Zone.DISCARD, None), False, True),
    "重铸（铁索连环）": ((Zone.HAND, ME), (Zone.DISCARD, None), False, True),
    "装备一张手牌（手牌 → 装备区）": ((Zone.HAND, ME), (Zone.EQUIP, ME), False, True),
    "顶替装备（旧装备进弃牌堆）": ((Zone.EQUIP, ME), (Zone.DISCARD, None), False, True),
    "装备被顺手牵羊拿走": ((Zone.EQUIP, ME), (Zone.HAND, OTHER), False, True),
    "谦逊扣牌（手牌 → 武将牌上）": ((Zone.HAND, ME), (Zone.HERO, ME), False, True),
    "从武将牌上取回手牌": ((Zone.HERO, ME), (Zone.HAND, ME), True, False),
    "武将牌上的牌进弃牌堆": ((Zone.HERO, ME), (Zone.DISCARD, None), False, False),
    "判定牌进弃牌堆": ((Zone.PROCESSING, None), (Zone.DISCARD, None), False, False),
    "判定区（乐）结算后进弃牌堆": ((Zone.JUDGE, ME), (Zone.DISCARD, None), False, False),
    "判定区被顺手牵羊拿走": ((Zone.JUDGE, ME), (Zone.HAND, OTHER), False, False),
    "天妒获得判定牌": ((Zone.PROCESSING, None), (Zone.HAND, ME), True, False),
    "结姻置入装备（我的手牌 → 他装备区，对给出方）": (
        (Zone.HAND, ME), (Zone.EQUIP, OTHER), False, True,
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_the_effect_registry_classifies_gain_and_loss_as_ruled(name: str) -> None:
    """登记表逐行核：每个效果对 ME 的 (获得, 失去) 与两个判据一致。"""
    from_zone, to_zone, expected_gain, expected_loss = CASES[name]
    event = _move(from_zone, to_zone)
    assert gains_card(event, ME) is expected_gain, f"{name}：获得 判错了"
    assert loses_card(event, ME) is expected_loss, f"{name}：失去 判错了"


def test_gain_is_any_move_into_my_hand_regardless_of_the_source() -> None:
    """Q1 + Q3 的边界：进我手牌就算获得（**来源不限，牌堆也是**）；进装备区不算。"""
    sources: tuple[tuple[Zone, int | None], ...] = (
        (Zone.DECK, None),
        (Zone.DISCARD, None),
        (Zone.PROCESSING, None),
        (Zone.HAND, OTHER),
        (Zone.EQUIP, OTHER),
        (Zone.JUDGE, OTHER),
        (Zone.HERO, OTHER),
    )
    for source in sources:
        assert gains_card(_move(source, (Zone.HAND, ME)), ME), source
        assert not gains_card(_move(source, (Zone.HAND, OTHER)), ME), source
        assert not gains_card(_move(source, (Zone.EQUIP, ME)), ME), (
            f"{source} → 我的装备区：Q3 明确「获得」只指进手牌"
        )
    assert gains_card(_move((Zone.DECK, None), (Zone.HAND, ME)), ME), (
        "牌堆 → 手牌也是获得：用户裁定 Q1（票 25 打开）"
    )


def test_loss_is_any_move_out_of_my_hand_or_equipment_regardless_of_target() -> None:
    """Q2 + Q3：**去向不限**——弃牌堆 / 处理区 / 别人手牌 / 装备区 / 武将牌上，离开就算。"""
    targets: tuple[tuple[Zone, int | None], ...] = (
        (Zone.DISCARD, None),
        (Zone.PROCESSING, None),
        (Zone.HAND, OTHER),
        (Zone.EQUIP, ME),
        (Zone.EQUIP, OTHER),
        (Zone.HERO, ME),
        (Zone.JUDGE, OTHER),
        (Zone.DECK, None),
    )
    for target in targets:
        assert loses_card(_move((Zone.HAND, ME), target), ME), target
        assert loses_card(_move((Zone.EQUIP, ME), target), ME), target
        assert not loses_card(_move((Zone.HAND, OTHER), target), ME), (
            f"别人的手牌 → {target} 是我以外的人失去，不算我的"
        )


def test_a_rearrangement_inside_my_own_hand_is_not_a_loss() -> None:
    """但书（票 07 收敛后的唯一边界）：手牌 → **自己的**手牌没有离开，不算失去。

    去向是手牌时只有"座位仍是我"才不算；交给**别人**的手牌照算（同一判据的两侧，见
    :func:`test_loss_is_any_move_out_of_my_hand_or_equipment_regardless_of_target`）。
    界【连营】在票 07 接到这条判据上后，靠这条但书挡住"任何手牌去向都算失去"的误读。
    """
    assert not loses_card(_move((Zone.HAND, ME), (Zone.HAND, ME)), ME), (
        "手牌 → 自己的手牌（重排一类）没有离开 ⇒ 不算失去"
    )


def test_only_hand_and_equipment_lose_cards() -> None:
    """Q4 / Q5：**判定区、武将牌上、处理区的牌离开不算失去**（判定牌进弃牌堆也不算）。"""
    sources: tuple[tuple[Zone, int | None], ...] = (
        (Zone.JUDGE, ME),
        (Zone.HERO, ME),
        (Zone.PROCESSING, None),
        (Zone.DECK, None),
        (Zone.DISCARD, None),
    )
    for source in sources:
        for target in ((Zone.DISCARD, None), (Zone.HAND, OTHER), (Zone.HAND, ME)):
            assert not loses_card(_move(source, target), ME), (source, target)

