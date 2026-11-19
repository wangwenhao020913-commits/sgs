"""``RunState`` 的形状与往返（M7 票 16；票 04 §1 的字段表）。

只经 ``sgs.script`` 公开接缝。断言分三层：

1. **字段表**：``RunState.model_fields`` 的字段名与顺序 = 票 04 §1 的表，一个不多一个不少；
2. **往返**：``model_dump(mode="json")`` → ``model_validate`` → **逐字段相等**（票 18 的序列化
   在它之上，本文件不碰 JSON 文本 / 文件 / 版本门）；
3. **形状常量**：背包 24 格 + 4 个出战槽、遗物 ≤6、``node_status`` 的键是
   ``"<layer_id>/<node_id>"``、``pending.spec`` 五选一（battle / event / shop / unknown /
   揭晓后的内层）。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from sgs.data import Identity, PveConfig, PveVictory, PveVictoryKind
from sgs.script import (
    BACKPACK_SLOTS,
    EQUIPPED_SLOT_COUNT,
    MAX_RELICS,
    REWARD_OPTION_COUNT,
    SHOP_RELIC_OFFERS,
    SHOP_SKILL_OFFERS,
    Backpack,
    BattleSpec,
    EventSpec,
    NodeStatus,
    Pending,
    RevealedSpec,
    RunState,
    ShopSpec,
    ShopStock,
    UnknownSpec,
    progress_key,
)

FIELD_ORDER = (
    "run_id",
    "revision",
    "seed",
    "script_id",
    "script_version",
    "script_digest",
    "roster_candidates",
    "chosen_hero",
    "layer_index",
    "flags",
    "coins",
    "backpack",
    "relics",
    "node_status",
    "pending",
)
"""票 04 §1 的 ``run`` 字段表，按表里的书写顺序。"""

SLOTS: tuple[str | None, ...] = (
    "hero.liubei.rende",
    None,
    "hero.guanyu.wusheng",
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
)
"""24 格：两条技能在 0 / 2 号格，其余空着（前 4 格 = 出战槽）。"""


def _backpack(**overrides: Any) -> Backpack:
    return Backpack(slots=SLOTS, equipped_slots=(0, 1, 2, 3), **overrides)


def _run_state(**overrides: Any) -> RunState:
    """一份**合法**的 run 状态（形状照票 04 §1 的示例，内容是占位）。"""
    fields: dict[str, Any] = {
        "run_id": "1234567890-1",
        "revision": 7,
        "seed": 1234567890,
        "script_id": "huangjin",
        "script_version": 3,
        "script_digest": "sha256:" + "6f1c" * 16,
        "roster_candidates": ("nengren", "hero.liubei", "hero.guanyu"),
        "chosen_hero": "nengren",
        "layer_index": 0,
        "flags": ("saved_village",),
        "coins": 22,
        "backpack": _backpack(),
        "relics": ("relic.extra_draw",),
        "node_status": {
            progress_key("l1", "n3"): NodeStatus.COMPLETED,
            progress_key("l1", "n5"): NodeStatus.CLOSED,
        },
        "pending": None,
    }
    fields.update(overrides)
    return RunState(**fields)


def test_run_state_has_exactly_the_field_table_of_ticket_04() -> None:
    """字段名与顺序 = 票 04 §1 的 ``run`` 表（逐字段对齐，不多不少）。"""
    assert tuple(RunState.model_fields) == FIELD_ORDER


def test_run_state_round_trips_through_its_json_dump() -> None:
    """dump → load → 逐字段相等（票 18 的存档往返只在此之上加版本门与文件）。"""
    state = _run_state()

    dumped = state.model_dump(mode="json")
    loaded = RunState.model_validate(dumped)

    assert loaded == state
    assert [getattr(loaded, name) for name in FIELD_ORDER] == [
        getattr(state, name) for name in FIELD_ORDER
    ]


def test_the_json_dump_uses_the_documented_key_names_and_shapes() -> None:
    """``backpack{slots, equipped_slots}`` / ``node_status`` 的键形 / ``pending`` 的 null。"""
    dumped = _run_state().model_dump(mode="json")

    assert dumped["backpack"] == {
        "slots": list(SLOTS),
        "equipped_slots": [0, 1, 2, 3],
    }
    assert dumped["node_status"] == {"l1/n3": "completed", "l1/n5": "closed"}
    assert dumped["pending"] is None
    assert dumped["roster_candidates"] == ["nengren", "hero.liubei", "hero.guanyu"]


def test_progress_key_prefixes_the_layer_id() -> None:
    """``Node.id`` 是层内编号 ⇒ 存档进度键带层前缀（票 03 §8.2 / 票 04 §7 的示例）。"""
    assert progress_key("l1", "n7") == "l1/n7"


def test_a_full_backpack_is_exactly_24_slots() -> None:
    assert len(SLOTS) == BACKPACK_SLOTS == 24
    assert Backpack(slots=SLOTS).equipped_slots == (0, 1, 2, 3)


@pytest.mark.parametrize("count", [BACKPACK_SLOTS - 1, BACKPACK_SLOTS + 1])
def test_a_backpack_with_the_wrong_number_of_slots_is_rejected(count: int) -> None:
    with pytest.raises(ValidationError):
        Backpack(slots=tuple(None for _ in range(count)))


def test_there_are_exactly_four_equipped_slots() -> None:
    with pytest.raises(ValidationError):
        Backpack(slots=SLOTS, equipped_slots=(0, 1, 2))

    with pytest.raises(ValidationError):
        Backpack(slots=SLOTS, equipped_slots=(0, 1, 2, 3, 4))


def test_an_equipped_slot_may_not_point_outside_the_backpack_nor_repeat() -> None:
    with pytest.raises(ValidationError):
        Backpack(slots=SLOTS, equipped_slots=(0, 1, 2, BACKPACK_SLOTS))

    with pytest.raises(ValidationError):
        Backpack(slots=SLOTS, equipped_slots=(0, 0, 2, 3))


def test_the_equipped_slots_are_fixed_to_the_front_four() -> None:
    """出战槽 = **前 4 格**（票 02 §3；spec「24 格、其中前 4 格是出战槽」）。

    UI 从不移动出战槽（票 22 M7-22c：交换 / 移动只动 ``slots``，``equipped_slots`` 不动），
    所以模型只接受 ``(0, 1, 2, 3)`` 这一种写法：换成别的四格或换序都不再是"前 4 格"，
    而 ``equipped_skills`` 按 ``equipped_slots`` 次序读出战技能，换序会悄悄改顺序。
    """
    assert Backpack(slots=SLOTS).equipped_slots == (0, 1, 2, 3)
    assert Backpack(slots=SLOTS, equipped_slots=(0, 1, 2, 3)).equipped_slots == (0, 1, 2, 3)

    for slots in (
        (1, 2, 3, 4),  # 平移四格：不在前 4 格里
        (3, 2, 1, 0),  # 同一组四格但换了序
        (0, 1, 2, 5),  # 只有三格在前 4 格
    ):
        with pytest.raises(ValidationError):
            Backpack(slots=SLOTS, equipped_slots=slots)


def test_at_most_six_relics() -> None:
    relics = tuple(f"relic.{index}" for index in range(MAX_RELICS))
    assert _run_state(relics=relics).relics == relics

    with pytest.raises(ValidationError):
        _run_state(relics=relics + ("relic.one_too_many",))


def test_exactly_three_roster_candidates() -> None:
    """3 选 1 的候选恒为 3 名（票 04 §1 的 ``×3``；剧本库的 roster 也是 3 名）。"""
    for candidates in (("a", "b"), ("a", "b", "c", "d")):
        with pytest.raises(ValidationError):
            _run_state(roster_candidates=candidates)


def test_extra_fields_are_rejected() -> None:
    """``extra="forbid"``：拼错的键当场判红（票 04 §1 的"不进存档"清单不会悄悄进档）。"""
    with pytest.raises(ValidationError):
        _run_state(available_next=("l1/n7",))


def test_the_engine_seed_range_is_already_enforced_by_the_state() -> None:
    """引擎边界校验 seed ∈ [0, 2**64)（票 03 的事实复核）；状态模型不另立尺度。"""
    with pytest.raises(ValidationError):
        _run_state(seed=-1)

    with pytest.raises(ValidationError):
        _run_state(seed=2**64)


# ---------------------------------------------------------------------------
# pending.spec：五选一（票 04 §1 的表）
# ---------------------------------------------------------------------------


def _pve() -> PveConfig:
    """一份最小但合法的冻结 PvE 配置（形状照票 04 §7 的示例，只留必要字段）。"""
    return PveConfig(
        identities=(Identity.ALLY, Identity.ENEMY),
        principal=0,
        victory=PveVictory(kind=PveVictoryKind.ROUT),
    )


def _battle_spec() -> BattleSpec:
    return BattleSpec(
        kind="battle",
        battle_seed=8811223344,
        pve=_pve(),
        rewards=(("hero.zhangfei.paoxiao", "hero.zhaoyun.longdan"),),
    )


def _event_spec() -> EventSpec:
    return EventSpec(
        kind="event",
        event_id="ev.village",
        outcome_rolls={0: {"coins": 2}, 2: {"skill": "hero.liubei.rende"}},
    )


def _shop_spec() -> ShopSpec:
    return ShopSpec(
        kind="shop",
        stock=ShopStock(
            skills=("hero.liubei.rende", "hero.guanyu.wusheng"),
            relics=("relic.extra_draw",),
        ),
    )


def _unknown_spec() -> UnknownSpec:
    return UnknownSpec(
        kind="unknown",
        revealed=RevealedSpec(room="room.ambush", spec=_battle_spec()),
    )


def _round_trip(pending: Pending) -> Pending:
    return Pending.model_validate(pending.model_dump(mode="json"))


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(_battle_spec(), id="battle"),
        pytest.param(_event_spec(), id="event"),
        pytest.param(_shop_spec(), id="shop"),
        pytest.param(_unknown_spec(), id="unknown"),
    ],
)
def test_each_pending_spec_round_trips_through_its_json_dump(spec: Any) -> None:
    """四类节点 spec + 揭晓内层：dump → load → 逐字段相等（票 18 的往返在此之上）。"""
    pending = Pending(node_id="l1/n7", spec=spec)

    loaded = _round_trip(pending)

    assert loaded == pending
    assert type(loaded.spec) is type(spec)


def test_the_event_spec_keeps_option_indices_as_integer_keys() -> None:
    """``outcome_rolls`` 的键是**选项下标**（JSON 里是字符串键，模型还原成 int）。"""
    loaded = _round_trip(Pending(node_id="l1/n2", spec=_event_spec()))

    assert isinstance(loaded.spec, EventSpec)
    assert loaded.spec.outcome_rolls == {0: {"coins": 2}, 2: {"skill": "hero.liubei.rende"}}


def test_the_revealed_inner_spec_names_the_room_it_revealed() -> None:
    """揭晓结果 = 房间 id + 内层 spec（"按内层房间的流程走"，票 22）。"""
    loaded = _round_trip(Pending(node_id="l1/n4", spec=_unknown_spec()))

    assert isinstance(loaded.spec, UnknownSpec)
    assert loaded.spec.revealed.room == "room.ambush"
    assert type(loaded.spec.revealed.spec) is BattleSpec


def test_a_question_room_may_not_reveal_another_question_room() -> None:
    """问号池禁止递归（票 03 §2）⇒ 内层 spec 的类型里没有 ``unknown``。"""
    with pytest.raises(ValidationError):
        UnknownSpec(kind="unknown", revealed=RevealedSpec(room="r", spec=_unknown_spec()))


def test_a_run_state_with_a_pending_node_round_trips_field_by_field() -> None:
    """带 ``pending`` 的整份 ``RunState`` 往返（节点 = 事务的 S_enter 态）。"""
    state = _run_state(pending=Pending(node_id="l1/n7", spec=_unknown_spec()))

    loaded = RunState.model_validate(state.model_dump(mode="json"))

    assert loaded == state
    assert loaded.pending is not None
    assert loaded.pending.node_id == "l1/n7"
    assert loaded.pending.spec == state.pending.spec


def test_a_pending_spec_must_name_a_known_kind() -> None:
    with pytest.raises(ValidationError):
        UnknownSpec(kind="boss", revealed=RevealedSpec(room="r", spec=_shop_spec()))


def test_a_pending_spec_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Pending(node_id="l1/n7", spec=_shop_spec(), cursor=3)


def test_a_reward_pick_offers_at_most_three_candidates() -> None:
    ok = tuple(f"skill.{index}" for index in range(REWARD_OPTION_COUNT))
    assert BattleSpec(kind="battle", battle_seed=1, pve=_pve(), rewards=(ok,)).rewards == (ok,)

    with pytest.raises(ValidationError):
        BattleSpec(kind="battle", battle_seed=1, pve=_pve(), rewards=(ok + ("skill.extra",),))


def test_a_shop_stock_pins_the_upper_bounds() -> None:
    skills = tuple(f"skill.{index}" for index in range(SHOP_SKILL_OFFERS))
    relics = tuple(f"relic.{index}" for index in range(SHOP_RELIC_OFFERS))
    assert ShopStock(skills=skills, relics=relics).relics == relics

    with pytest.raises(ValidationError):
        ShopStock(skills=skills + ("skill.extra",))

    with pytest.raises(ValidationError):
        ShopStock(relics=relics + ("relic.extra",))


def test_a_battle_seed_is_a_64_bit_engine_seed() -> None:
    with pytest.raises(ValidationError):
        BattleSpec(kind="battle", battle_seed=2**64, pve=_pve())

