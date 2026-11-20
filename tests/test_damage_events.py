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
    run_turns,
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


# ---------------------------------------------------------------------------
# 票 03：本局"受到过伤害"的座位事实（神郭嘉【天翊】的觉醒条件）
# ---------------------------------------------------------------------------


def test_damage_taken_ever_is_set_by_the_same_write_point_as_the_turn_counter():
    engine = _engine_with_damage()

    _damage_until_end(engine)

    assert engine.state.player(1).damage_taken_this_turn == 1
    assert engine.state.player(1).damage_taken_ever is True, "整局事实与回合事实同一写点"
    assert engine.state.player(0).damage_taken_ever is False, "没挨打的人不置位"


def test_damage_taken_ever_ignores_prevented_and_zero_damage():
    engine = _engine_with_damage(nature=DamageNature.NORMAL)
    add_tag(engine.stack.top, Tag.PREVENTED)

    _damage_until_end(engine)

    assert engine.state.player(1).damage_taken_ever is False, "被防止的伤害不算受过伤"

    engine = _engine_with_damage(amount=0)
    _damage_until_end(engine)
    assert engine.state.player(1).damage_taken_ever is False, "0 点伤害不算受过伤"


def test_damage_taken_ever_counts_armor_absorbed_damage():
    """与 ``damage_taken_this_turn`` 同判据：护甲吸收的部分不减"是否受过伤"。"""
    engine = _engine_with_damage(armor=(0, 2))

    _damage_until_end(engine)

    assert engine.state.player(1).hp == 4, "伤害全被护甲吸收"
    assert engine.state.player(1).damage_taken_this_turn == 1
    assert engine.state.player(1).damage_taken_ever is True


def test_damage_taken_ever_survives_turn_end():
    """整局事实**不进** ``_clear_turn_effects``：跑完一个回合仍然为真。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=deck_excluding(PACK),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    state.player(1).damage_taken_ever = True

    run_turns(engine, [PassPlayer(), PassPlayer()], turns=1)

    assert state.player(1).damage_taken_ever is True, "回合末不得清整局事实"
    assert state.player(1).damage_taken_this_turn == 0, "本回合事实照旧被清"


# ---------------------------------------------------------------------------
# "伤害 → 失去体力"的整段改写（yijiang-2011-xusheng-chunhua 票 90 的框架件）
# ---------------------------------------------------------------------------

REWRITE_SKILL = "hero.fake.damage_rewrite"
"""改写协议（``damage_rewritten`` + ``damage_replacement_amount``）用的合成技能 id。

只活在 ``CATALOG`` 的**临时**注入里（与 ``tests/support/positions.py`` 的
``state_engine`` / ``damage_window_engine`` 同一姿态）：改写是**引擎的通用协议**，不是按
技能名特判，故这里用一条假技能证明"谁来写这两个帧私有槽都行"；首个真实载体 = 票 02 的
张春华移动版【绝情】。
"""


def _rewrite_engine(*, amount: int = 2, replacement: int = 2, armor: tuple[int, int] = (0, 0)):
    """座位 0 = 持合成技能的来源、座位 1 = 目标；压一条 ``amount`` 点的 ``DAMAGE`` 子帧。

    返回 ``(engine, restore)``：``CATALOG`` 的临时替换必须由调用方在断言后恢复
    （``try/finally`` 写在用例里，读起来比藏进 helper 清楚）。
    """
    from types import MappingProxyType

    from sgs.effects import CarrierKind
    from sgs.skills import Hook, SkillDef
    from sgs.skills import catalog as catalog_module
    from sgs.engine import steps as steps_module

    def handler(host, frame):
        del host
        frame.data[steps_module.DAMAGE_REWRITTEN_KEY] = True
        frame.data[steps_module.DAMAGE_REPLACEMENT_AMOUNT_KEY] = replacement
        return None

    definition = SkillDef(
        skill_id=REWRITE_SKILL,
        locked=True,
        carrier=CarrierKind.HERO,
        hooks=(Hook(Timing.DAMAGE_BEFORE_START, handler=handler),),
    )
    saved = catalog_module.CATALOG
    catalog_module.CATALOG = MappingProxyType({**saved, REWRITE_SKILL: definition})

    def restore() -> None:
        catalog_module.CATALOG = saved

    specs = [SeatSpec(hp=4, hp_max=4), SeatSpec(hp=4, hp_max=4)]
    state = build_state(pack=PACK, seats=specs, deck=deck_excluding(PACK))
    for seat, value in enumerate(armor):
        state.players[seat].armor = value
    engine = Engine.from_state(state, seed=7)
    engine.grant_skill(0, REWRITE_SKILL)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(amount=amount, nature=DamageNature.FIRE),
        timing=Timing.DAMAGE_DEAL,
    )
    return engine, restore


def test_a_rewritten_damage_event_becomes_a_life_decrease():
    """**事件不在**：没有伤害事实、没有"造成 / 受到伤害后"，只有一条失去体力。

    `三国杀详细规则.txt:378–383`（§4.14 时机 0)）："触发后不再进行伤害结算，本该受到伤害的
    角色改为失去等同于伤害值基数的体力"。
    """
    engine, restore = _rewrite_engine(amount=2, replacement=2)
    try:
        run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: ran_timing(e, Timing.LIFE_DECREASE_AFTER))
    finally:
        restore()

    assert engine.state.player(1).hp == 2, "改写后按**基数**扣体力（2 点）"
    assert tuple(engine.damage_events) == (), "伤害事件整段不发生 ⇒ 一条伤害事实都不许记"
    assert not ran_timing(engine, Timing.DAMAGE_AFTER_TAKE), "受到伤害后不再派发"
    assert not ran_timing(engine, Timing.DAMAGE_AFTER_DEAL), "造成伤害后不再派发"
    assert ran_timing(engine, Timing.DAMAGE_BEFORE_START), "改写发生在时机 0)，记录照落一条"


def test_a_rewritten_damage_event_does_not_touch_armor():
    """失去体力**不碰护甲**（账本「护甲值 = 独立第二轴」）：目标有 2 点护甲，也照扣 2 点体力。"""
    engine, restore = _rewrite_engine(amount=2, replacement=2, armor=(0, 2))
    try:
        run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: ran_timing(e, Timing.LIFE_DECREASE_AFTER))
    finally:
        restore()

    assert engine.state.player(1).armor == 2, "护甲一点不动（这不是伤害）"
    assert engine.state.player(1).hp == 2, "体力照扣基数"
    assert _deltas(engine, "armor_change") == [], "连一条 ``armor_change`` delta 都不该有"


def test_the_rewrite_amount_is_independent_of_the_damage_amount():
    """"失去多少"读**改写槽**，不读伤害帧的 ``amount``（两者可以不同）。"""
    engine, restore = _rewrite_engine(amount=3, replacement=1)
    try:
        run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: ran_timing(e, Timing.LIFE_DECREASE_AFTER))
    finally:
        restore()

    assert engine.state.player(1).hp == 3, "只失去改写槽写的 1 点，不是伤害帧的 3 点"
