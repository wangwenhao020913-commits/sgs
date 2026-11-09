"""伤害事实缓冲 ``Engine.damage_events``（damage-fx 票 01 / spec §Solution 1）。

口径（票面「验收」逐字）：

- 一次**伤害事件** ⇒ 恰好一条 :class:`~sgs.engine.game.DamageEvent`：
  ``seq`` / ``source`` / ``target`` / ``amount`` / ``nature`` 五项齐全；
- 记录面**不筛属性**：火焰伤害照样记一条（筛选是表现层的事）；
- ``amount`` 是**全额**：护甲吸收的部分不减，哪怕这一帧一条 ``hp_change`` 都不发；
- 「失去体力」（``LIFE_DECREASE`` 帧但不是伤害）**不**产生事件。

两条取数路径（``CODING_STANDARDS.md`` §6「读取方断言走真实产出路径」）：

- 普通【杀】命中走**真实使用链**（``start_use`` → RESOLVE → 闪窗口放弃 → DAMAGE），
  证明这条通道由产品路径真的写过；
- 其余几条走人为局面 + 直接压一个 ``FrameKind.DAMAGE`` 子帧（与引擎在【杀】命中时压的
  形状逐字相同，先例见 ``tests/test_armor.py``）：它们要钉的是"属性 / 无来源 / 护甲分账 /
  非伤害帧"这几件与使用链无关的事，跑整条【杀】只是绕远路。
"""

from __future__ import annotations

import pytest

from sgs.data import load_content_pack
from sgs.effects import DamageNature
from sgs.engine import Engine
from sgs.engine.game import DamageEvent
from sgs.resolution import Tag, add_tag
from sgs.state import FrameKind
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
"""一张实体【杀】：真实使用链的产物，也是人为伤害帧的渠道牌。"""


def _deltas(engine: Engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def _damage_frame(*, amount: int, nature: object, source: int | None = 0) -> dict:
    """与引擎在【杀】命中时压的 ``DAMAGE`` 子帧逐字同形（无渠道牌，渠道与本票无关）。"""
    return {
        "source": source,
        "targets": (1,),
        "card": None,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": 1,
        "channel": None,
        "nature": nature,
        "chain": False,
        "amount": amount,
    }


def _engine_with_damage(
    *,
    hp=(4, 4),
    hp_max=4,
    armor=(0, 0),
    amount: int = 1,
    nature: object = DamageNature.NORMAL,
    source: int | None = 0,
) -> Engine:
    """人为局面：座位 0 = 来源、座位 1 = 目标；压一条 ``amount`` 点的 ``DAMAGE`` 子帧。"""
    specs = [SeatSpec(hp=hp[index], hp_max=hp_max) for index in range(len(hp))]
    state = build_state(pack=PACK, seats=specs, deck=deck_excluding(PACK))
    for seat, value in enumerate(armor):
        state.players[seat].armor = value
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(amount=amount, nature=nature, source=source),
        timing=Timing.DAMAGE_DEAL,
    )
    return engine


def _damage_until_end(engine: Engine) -> Engine:
    return run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.DAMAGE_END),
    )


# ---------------------------------------------------------------------------
# 真实使用链：【杀】命中 ⇒ 恰好一条，五项齐全
# ---------------------------------------------------------------------------


def _real_slash_engine() -> Engine:
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,)), SeatSpec(hp=4)],
        deck=deck_excluding(PACK, SLASH),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=11)
    engine.start_use(state.card(SLASH), source=0, targets=(1,))
    return engine


def test_a_real_slash_hit_records_exactly_one_damage_fact():
    engine = _real_slash_engine()

    _damage_until_end(engine)

    assert len(engine.damage_events) == 1, "一次伤害事件 ⇒ 恰好一条（不是逐点、不重复）"
    event = engine.damage_events[0]
    assert isinstance(event, DamageEvent)
    assert (event.source, event.target) == (0, 1), "剑光要从出牌方飞到中剑方：来源与目标都在"
    assert event.amount == 1, "【杀】的伤害值是 1"
    assert event.nature is DamageNature.NORMAL, "无属性【杀】⇒ NORMAL（表现层只播这一档）"
    assert engine.state.player(1).hp == 3, "规则面照常结算（这条缓冲不替代任何判据）"


def test_the_anchor_seq_is_a_record_that_really_exists_in_this_batch():
    """``seq`` 与 ``skill_events`` 同一口径：锚在**已有的**那条记录上，事件本身不是记录。"""
    engine = _real_slash_engine()

    _damage_until_end(engine)

    seqs = {record.seq for record in engine.records if getattr(record, "seq", None) is not None}
    assert engine.damage_events[0].seq in seqs, (
        "锚点必须是本批里真实存在的一条记录：前端「回放到它才演」才有落点"
    )
    assert all(record.kind != "damage_event" for record in engine.records), (
        "伤害事实不进记录流（进了就会改 47 条金录像的字节）"
    )


# ---------------------------------------------------------------------------
# 记录面不筛属性 / 不猜来源
# ---------------------------------------------------------------------------


def test_a_fire_hit_is_recorded_too_because_the_buffer_never_filters_nature():
    engine = _engine_with_damage(nature=DamageNature.FIRE)

    _damage_until_end(engine)

    assert len(engine.damage_events) == 1, "火焰伤害照样记一条"
    assert engine.damage_events[0].nature is DamageNature.FIRE, "属性原样透传"
    assert engine.state.player(1).hp == 3, "火杀照常扣血（记录面与规则面互不影响）"


def test_a_thunder_hit_keeps_its_own_nature():
    engine = _engine_with_damage(nature=DamageNature.THUNDER)

    _damage_until_end(engine)

    assert [event.nature for event in engine.damage_events] == [DamageNature.THUNDER]


def test_a_nature_less_hit_keeps_none_instead_of_guessing_normal():
    """``nature`` 缺声明（拼点类伤害帧）⇒ 原样留 ``None``，引擎不替它猜一个属性。"""
    engine = _engine_with_damage(nature=None)

    _damage_until_end(engine)

    assert len(engine.damage_events) == 1
    assert engine.damage_events[0].nature is None


def test_a_source_less_hit_records_no_source_instead_of_guessing_one():
    """无来源伤害（判定类）的 ``source`` = ``None``：前端因此"不猜来源"，剑光从画面外飞入。"""
    engine = _engine_with_damage(nature=DamageNature.NORMAL, source=None)

    _damage_until_end(engine)

    assert len(engine.damage_events) == 1
    assert engine.damage_events[0].source is None


# ---------------------------------------------------------------------------
# 护甲分账：amount 是全额
# ---------------------------------------------------------------------------


def test_armor_absorbing_the_whole_hit_still_records_the_full_amount():
    engine = _engine_with_damage(hp=(4, 3), armor=(0, 2), amount=2)

    _damage_until_end(engine)

    assert _deltas(engine, "armor_change"), "护甲照常落记录"
    assert not _deltas(engine, "hp_change"), "护甲全吸收 ⇒ 一条 hp_change 都不发（本帧口径）"
    assert len(engine.damage_events) == 1, "全吸收也是一次伤害事件，照样记一条"
    assert engine.damage_events[0].amount == 2, "记录的是**全额**伤害，不是掉的血"


def test_armor_partially_absorbing_keeps_the_full_amount():
    engine = _engine_with_damage(hp=(4, 2), armor=(0, 1), amount=2)

    _damage_until_end(engine)

    assert (engine.state.player(1).hp, engine.state.player(1).armor) == (1, 0)
    assert engine.damage_events[0].amount == 2, "护甲吸掉 1 点不影响记录面（票 01 验收第 3 条）"


# ---------------------------------------------------------------------------
# 不是伤害事件就不记
# ---------------------------------------------------------------------------


def test_losing_hp_is_not_a_damage_event():
    """「失去体力」压的是 ``LIFE_DECREASE`` 帧、没有 ``DAMAGE`` 帧 ⇒ 一条都不记。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], deck=deck_excluding(PACK))
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.LIFE_DECREASE,
        {"target": 1, "source": 0, "amount": 2},
        timing=Timing.LIFE_DECREASE_BEFORE,
    )

    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.LIFE_DECREASE_AFTER),
    )

    assert engine.state.player(1).hp == 2, "体力照常掉 2 点"
    assert engine.damage_events == (), "「失去体力」不是伤害事件，剑光不该演"


def test_a_prevented_hit_is_not_a_damage_event():
    """被【寒冰剑】一类效果防止的伤害不是伤害事件：帧标签 ``PREVENTED`` ⇒ 数值不生效。"""
    engine = _engine_with_damage(nature=DamageNature.NORMAL)
    add_tag(engine.stack.top, Tag.PREVENTED)

    _damage_until_end(engine)

    assert engine.state.player(1).hp == 4, "防止伤害 ⇒ 体力一点不掉"
    assert engine.damage_events == (), "没落到实处的伤害不产生演出事件"


# ---------------------------------------------------------------------------
# 四档强度在记录面是同一条事实
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [1, 2, 3, 4])
def test_every_variant_records_the_same_single_fact_with_its_own_amount(amount: int):
    """1~4 点在记录面只差 ``amount``：档位是表现层的判断，引擎不分档。"""
    engine = _engine_with_damage(hp=(4, 8), hp_max=8, amount=amount)

    _damage_until_end(engine)

    assert len(engine.damage_events) == 1
    assert engine.damage_events[0].amount == amount
