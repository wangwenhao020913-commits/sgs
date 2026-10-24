"""技能三态：**获得 / 失去 / 失效**（M6 票 04 的引擎原语）。

规则原文（§四 L528–L535「技能的发动与结算」）只规定"技能 = 发动时机 + 发动条件 + 消耗 +
效果"与技能类型（锁定 / 非锁定 / 限定 / 觉醒），**没有**"运行时技能实例怎么增删"的条文；
本票按 `06` §5「程序化约定与偏离一手」的裁定把它落成三种互不替代的状态（`GLOSSARY.md`「技能失效」「技能失去」）：

- **获得**（``SkillSourceKind.GRANTED``）：运行时授予的**第四种来源**，与实体装备牌 /
  虚拟装备 / 武将牌并列；实例因此有 ``skill_id`` 而没有 ``card_id``。
- **失去**（``Player.lost_skills``）：实例**不再存在**，且不可恢复 —— 武将牌上的技能清单
  一字未动，实例仍不产出。
- **失效**（``Player.suppressed_skills``）：实例**还在**，被座位级谓词挡在解析之外；
  "该座位所有非锁定技"这一档因此把**后获得**的技能一并挡掉。

解析顺序钉死为 **载体并集 → 去重 → 减失去 → 过失效谓词**（票面 §要做什么），本文件逐段
钉住那四步的可观测后果：

- 载体并集 / 去重：``resolve_instances`` 的 ``source.kind`` 与"同 id 只留最早来源"；
- 减失去：连续型效果（这里用【马术】的距离 −1）**立刻**回到基线；
- 失效谓词：锁定技不受"非锁定"谓词影响，非锁定技（含后获得的）一律不产出。

M6 的**获得 / 失去**：票 04 时两者都零载体，故只有结构化构造测试（按 `06` §5「程序化约定与偏离一手」的零载体规格登记）；票 06 的 ☆ 派生技能（【荐言】/【攻心】）
是**获得**的第一个真实载体（觉醒技经 ``grant_skill`` 授予），其技能侧用例住
``tests/test_hero_jie.py``；**失去**在 M6 仍零载体（断肠 / 左慈留给后续里程碑）。
失效在 M6 有真实载体（票 04 的【义绝】/【铁骑】）。
"""

from __future__ import annotations

import pytest

from sgs.data import RuleConfig, load_content_pack
from sgs.effects import distance
from sgs.engine import Engine
from sgs.skills import SkillSourceKind, resolve_instances
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, run_until

PACK = load_content_pack(deck_profile="standard_108")

MASHU = "hero.machao.mashu"
"""【马术】的**锁定技**实例：距离 −1 是"查询期从活着的实例算"的连续型效果（D9）。"""

LONGDAN = "hero.zhaoyun.longdan"
"""【龙胆】的**非锁定技**实例：失效谓词"所有非锁定技"的判据样本。"""

WUSHENG = "hero.guanyu.wusheng"
"""【武圣】同为非锁定技（第二个样本：一条失效记录盖住同一座位的多条技能）。"""

YIJUE = "hero.jie_guanyu.yijue"
"""失效记录的**施加者技能 id**（义绝的实例 id；本文件的引擎级构造不要求它已登记）。"""


def _engine(heroes: tuple[str | None, ...] = (), *, seats: int = 4) -> Engine:
    """``seats`` 个座位的构造局面（座位 0 起按 ``heroes`` 给武将，其余无武将）。

    牌堆给满（构造局面不摆起始手牌）：本文件的"回合末清理"用例要真的走完一个回合，
    空牌堆会在摸牌阶段触发"短摸即平局"。
    """
    specs = [
        SeatSpec(hero=heroes[index] if index < len(heroes) else None)
        for index in range(seats)
    ]
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=seats),
    )
    return Engine.from_state(state, seed=7)


def _instances(state, seat: int) -> set[str]:
    return {
        instance.skill_id
        for instance in resolve_instances(state)
        if instance.owner == seat
    }


def _deltas(engine: Engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == delta_type
    ]


# ---------------------------------------------------------------------------
# 获得：第四种来源
# ---------------------------------------------------------------------------


def test_a_granted_skill_is_a_fourth_source_and_carries_the_continuous_effect():
    engine = _engine()
    state = engine.state
    baseline = distance(state, 0, 2)
    assert baseline == 2, "4 人环上 0 到 2 的座位距离 = 2（无坐骑）"

    assert engine.grant_skill(0, MASHU) is True

    instances = [
        instance
        for instance in resolve_instances(state)
        if instance.owner == 0 and instance.skill_id == MASHU
    ]
    assert len(instances) == 1
    assert instances[0].source.kind is SkillSourceKind.GRANTED, "获得是第四种来源"
    assert instances[0].source.card_id is None, "获得不是一张牌（不造 CardRef）"
    assert distance(state, 0, 2) == baseline - 1, "连续型效果随实例在查询期生效"

    gains = _deltas(engine, "skill_gain")
    assert [(record.seat, record.skill_id) for record in gains] == [(0, MASHU)]


def test_granting_the_same_skill_twice_keeps_one_instance_and_one_delta():
    engine = _engine()
    assert engine.grant_skill(0, MASHU) is True
    assert engine.grant_skill(0, MASHU) is False, "重复授予不是一次新的状态变化"

    assert [record.skill_id for record in _deltas(engine, "skill_gain")] == [MASHU]
    assert len(
        [
            instance
            for instance in resolve_instances(engine.state)
            if instance.owner == 0 and instance.skill_id == MASHU
        ]
    ) == 1, "同 owner 同技能只留一个实例（与其余三种来源同一条去重口径）"


def test_a_granted_skill_is_suppressed_like_any_other_instance():
    """获得来的实例与其它来源同一条解析路径：失效谓词不区分来源。"""
    engine = _engine()
    engine.grant_skill(1, LONGDAN)
    assert LONGDAN in _instances(engine.state, 1)

    engine.suppress_skills(1, YIJUE)

    assert LONGDAN not in _instances(engine.state, 1), "后获得的非锁定技同样被挡"


# ---------------------------------------------------------------------------
# 失去：实例移除、不可恢复、连续型效果立刻消失
# ---------------------------------------------------------------------------


def test_a_lost_skill_disappears_for_good_and_its_distance_modifier_goes_with_it():
    engine = _engine(("machao",))
    state = engine.state
    assert MASHU in _instances(state, 0)
    assert distance(state, 0, 2) == 1, "马术的距离 −1 生效中"

    assert engine.lose_skill(0, MASHU) is True

    assert MASHU not in _instances(state, 0), "失去 = 实例不再存在"
    assert distance(state, 0, 2) == 2, "连续型效果随实例消失（距离回到基线）"
    assert engine.lose_skill(0, MASHU) is False, "重复失去不是一次新的状态变化"

    losses = _deltas(engine, "skill_lose")
    assert [(record.seat, record.skill_id) for record in losses] == [(0, MASHU)]


def test_a_lost_skill_cannot_come_back_even_though_the_hero_card_still_lists_it():
    """不可恢复：武将牌上的技能清单**一字未动**，实例照样不产出。"""
    engine = _engine(("machao",))
    engine.lose_skill(0, MASHU)

    assert engine.state.player(0).hero == "machao", "失去不是改武将数据"
    assert engine.grant_skill(0, MASHU) is True, "再授予是另一条记录"
    assert MASHU not in _instances(engine.state, 0), "已失去的技能不会被重新授予复活"


def test_losing_a_skill_prunes_its_per_instance_state():
    """失去是"来源消失"：该实例的 ``skill_state`` 键随之清掉（与装备离场同一条清理）。"""
    engine = _engine(("machao",))
    instance = next(
        item
        for item in resolve_instances(engine.state)
        if item.owner == 0 and item.skill_id == MASHU
    )
    from sgs.skills import instance_key

    key = instance_key(instance)
    engine.state.skill_state[key] = 3

    engine.lose_skill(0, MASHU)

    assert key not in engine.state.skill_state


# ---------------------------------------------------------------------------
# 失效：座位级谓词（锁定技不受影响、后获得的技能一并被挡）
# ---------------------------------------------------------------------------


def test_suppression_is_seat_level_and_only_covers_non_locked_skills():
    engine = _engine(("machao", "guanyu"))
    state = engine.state
    assert engine.suppress_skills(1, YIJUE) is True

    suppressed = _instances(state, 1)
    assert WUSHENG not in suppressed, "武圣（非锁定）被挡"

    # 同一座位再获得一条非锁定技 ⇒ **同样**被这一条记录挡住（不逐技能展开）。
    engine.grant_skill(1, LONGDAN)
    assert LONGDAN not in _instances(state, 1)

    # 锁定技不受"所有**非锁定**技"这条谓词影响（马术照常产出）。
    engine.grant_skill(1, MASHU)
    assert MASHU in _instances(state, 1), "谓词只覆盖非锁定技"

    records = _deltas(engine, "skill_suppress")
    assert len(records) == 1
    assert (records[0].seat, records[0].skill_id) == (1, YIJUE)
    assert records[0].suppressed is True
    assert records[0].predicate.value == "all_non_locked"
    assert records[0].scope.value == "turn"


def test_suppressing_the_same_seat_twice_does_not_stack_a_second_record():
    engine = _engine()
    assert engine.suppress_skills(1, YIJUE) is True
    assert engine.suppress_skills(1, YIJUE) is False

    assert len(_deltas(engine, "skill_suppress")) == 1


def test_suppression_expires_at_the_end_of_the_turn():
    engine = _engine(("guanyu",), seats=2)
    engine.suppress_skills(1, YIJUE)
    assert WUSHENG in _instances(engine.state, 0), "被失效的是座位 1，座位 0 不受影响"

    players = [PassPlayer() for _ in engine.state.players]
    run_until(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
            for record in e.records
        ),
    )

    clears = [
        record for record in _deltas(engine, "skill_suppress") if record.suppressed is False
    ]
    assert [(record.seat, record.skill_id) for record in clears] == [(1, YIJUE)]
    assert not engine.state.player(1).suppressed_skills, "作用域 = 本回合，回合末清空"


# ---------------------------------------------------------------------------
# 工程错误：施加者 / 座位越界在写入口就炸
# ---------------------------------------------------------------------------


def test_unknown_seats_are_rejected_by_the_write_entries():
    engine = _engine()
    with pytest.raises(IndexError):
        engine.grant_skill(9, MASHU)
    with pytest.raises(IndexError):
        engine.lose_skill(9, MASHU)
    with pytest.raises(IndexError):
        engine.suppress_skills(9, YIJUE)
