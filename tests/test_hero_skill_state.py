"""技能三态：**获得 / 失去 / 失效**（M6 票 04 的引擎原语）。

规则原文（§四 L541–L551「技能的发动与结算」）只规定"技能 = 发动时机 + 发动条件 + 消耗 +
效果"与技能类型（锁定 / 非锁定 / 限定 / 觉醒），**没有**"运行时技能实例怎么增删"的条文；
本票按 `06` §5「程序化约定与偏离一手」的裁定把它落成三种互不替代的状态（`GLOSSARY.md`「技能失效」「技能失去」）：

- **获得**（``SkillSourceKind.GRANTED``）：运行时授予的**第四种来源**，与实体装备牌 /
  虚拟装备 / 武将牌并列；实例因此有 ``skill_id`` 而没有 ``card_id``。
- **失去**（``Player.lost_skills``）：实例**不再存在**，且不可恢复 —— 武将牌上的技能清单
  一字未动，实例仍不产出。
- **失效**（``Player.suppressed_skills``）：实例**还在**，被座位级谓词挡在解析之外；
  "该座位所有非锁定技"这一档因此把**后获得**的技能一并挡掉。用户裁定（票 09）：
  失效只作用于**武将技能**；装备牌 / 虚拟装备带来的技能不失效（本文件的来源过滤用例钉住）。

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

from sgs.data import Gender, Kingdom, RuleConfig, hero_by_id, load_content_pack
from sgs.effects import distance
from sgs.engine import Engine
from sgs.skills import SkillSourceKind, hero_card_zone_key, resolve_instances
from sgs.state.projection import project
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")

MASHU = "hero.machao.mashu"
"""【马术】的**锁定技**实例：距离 −1 是"查询期从活着的实例算"的连续型效果（D9）。"""

LONGDAN = "hero.zhaoyun.longdan"
"""【龙胆】的**非锁定技**实例：失效谓词"所有非锁定技"的判据样本。"""

WUSHENG = "hero.guanyu.wusheng"
"""【武圣】同为非锁定技（第二个样本：一条失效记录盖住同一座位的多条技能）。"""

YIJUE = "hero.jie_guanyu.yijue"
"""失效记录的**施加者技能 id**（义绝的实例 id；本文件的引擎级构造不要求它已登记）。"""

JIE_TIEQI = "hero.jie_machao.tieqi"
"""第二条座位级失效的施加者（界马超【铁骑】）：与【义绝】共用同一个谓词。"""

DOUBLE_SWORD = "equip.double_sword"
"""非锁定装备技能：用来钉"失效不作用于装备牌带来的技能"。"""

EIGHT_DIAGRAMS = "equip.eight_diagrams"
"""虚拟装备的判据样本（`VIRTUAL_GRANT`，但定义是装备技能）。"""


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
# 可逆的失去 / 化身库 / 性别势力（shan-baozuoci 票 02；左慈）
# ---------------------------------------------------------------------------

JIE_QIANXUN = "hero.jie_luxun.qianxun"
"""【谦逊】：已登记的放置类技能，用它把"可逆失去的后果链"里的武将区清理跑出来。"""


def test_a_revoked_skill_can_be_gained_again_and_runs_the_whole_cleanup():
    """可逆失去 = 从授予表移除、不写 ``lost_skills``；后果链与永久失去同一段（2018 左慈口径）。"""
    engine = _engine(seats=2)
    state = engine.state
    assert engine.grant_skill(0, JIE_QIANXUN) is True, "构造：先把【谦逊】授予座位 0"
    cards = tuple(state.deck[:2])
    engine.place_on_hero_card(
        None, owner=0, skill_id=JIE_QIANXUN, holder=0, cards=state.refs(cards)
    )
    key = hero_card_zone_key(0, JIE_QIANXUN)
    assert state.hero_zone(0, key) == cards, "构造：牌在座位 0 自己的武将牌上"
    engine.add_marker(
        None, 1, name="测试·化身", source_seat=0, source_skill_id=JIE_QIANXUN, count=1
    )

    assert engine.revoke_skill(0, JIE_QIANXUN) is True

    assert state.player(0).lost_skills == [], "可逆失去**不写** lost_skills"
    assert JIE_QIANXUN not in _instances(state, 0), "实例消失"
    assert state.hero_zone(0, key) == (), "武将区照常清（与永久失去同一条后果链）"
    assert state.player(1).markers == (), "它产生的标记照常清"
    losses = _deltas(engine, "skill_lose")
    assert [(record.seat, record.skill_id) for record in losses] == [(0, JIE_QIANXUN)]

    assert engine.grant_skill(0, JIE_QIANXUN) is True
    assert JIE_QIANXUN in _instances(state, 0), "再获得 ⇒ 同一个技能实例回来（左慈重选同一技能）"


def test_revoking_a_skill_that_was_not_granted_is_a_no_op():
    engine = _engine(("machao",))
    assert engine.revoke_skill(0, MASHU) is False, "武将牌自带的技能不是'获得来的''"
    assert engine.state.player(0).granted_skills == []
    assert _deltas(engine, "skill_lose") == []


def test_avatar_cards_are_held_per_seat_and_released_on_clear():
    """化身库：抽到的武将牌进座位手里、抽走的真名不再可抽；清空 = 解除禁用。"""
    engine = _engine(seats=2)
    drawn = engine.draw_hero_cards(2)
    assert len(drawn) == 2
    true_names = {hero_by_id(item).true_name for item in drawn}
    assert true_names <= set(engine.state.drawn_true_names), "抽到即入禁用集合"

    assert engine.gain_avatar_cards(0, drawn) == 2
    assert tuple(engine.state.player(0).avatar_cards) == drawn
    assert engine.gain_avatar_cards(0, drawn) == 0, "同一张不会重复进库（幂等）"

    assert engine.clear_avatar_cards(0) == drawn
    assert engine.state.player(0).avatar_cards == []
    assert engine.state.drawn_true_names == (), "失去【化身】⇒ 化身牌解除禁用（回到武将牌堆）"
    assert engine.clear_avatar_cards(0) == (), "空库再清是零动作"


def test_gender_and_kingdom_can_be_changed_at_runtime():
    engine = _engine(seats=2)
    state = engine.state

    assert engine.set_gender(0, Gender.FEMALE) is True
    assert engine.set_gender(0, Gender.FEMALE) is False, "同值不是一次变化"
    assert state.player(0).gender is Gender.FEMALE

    assert engine.set_kingdom(0, Kingdom.WU) is True
    assert engine.set_kingdom(0, Kingdom.WU) is False
    assert project(state, 1).players[0].kingdom is Kingdom.WU, "势力是公开事实（投影看得见）"


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


@pytest.mark.parametrize("suppressor", [YIJUE, JIE_TIEQI])
def test_suppression_covers_hero_skills_but_not_equipment_skills(suppressor):
    """用户裁定：失效只锁武将技能；装备技能（非锁定）照常参与解析。

    两条座位级失效的施加者【义绝】/【铁骑】共用同一个 ``_skill_is_suppressed``，
    故用参数化把"两处口径一致"钉在同一个断言上。
    """
    sword = cards_of_type(PACK, "double_sword")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="guanyu", equip=(sword,)), SeatSpec()],
        deck=deck_excluding(PACK, sword),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)
    assert {WUSHENG, DOUBLE_SWORD} <= _instances(state, 0)

    engine.suppress_skills(0, suppressor)

    remaining = _instances(state, 0)
    assert WUSHENG not in remaining, "武将技能（【武圣】）照常失效"
    assert DOUBLE_SWORD in remaining, "装备牌带来的技能不失效"


def test_virtual_equipment_is_immune_but_non_equipment_virtual_grants_follow_the_hero_rule():
    """`VIRTUAL_GRANT` 不是"只用于虚拟装备"：测试构造也用它授予非装备技能。

    因此不能把整个来源一刀切；按**定义**是否为装备技能区分：虚拟装备免疫，
    非装备的虚拟授予仍按武将技能处理（会被失效挡住）。
    """
    virtual_state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="guanyu", virtual_equipment=(EIGHT_DIAGRAMS,)), SeatSpec()],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    virtual_engine = Engine.from_state(virtual_state, seed=7)
    virtual_engine.suppress_skills(0, YIJUE)
    assert WUSHENG not in _instances(virtual_state, 0)
    assert EIGHT_DIAGRAMS in _instances(virtual_state, 0), "虚拟装备同样不失效"

    grant_state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="guanyu", virtual_equipment=(LONGDAN,)), SeatSpec()],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    grant_engine = Engine.from_state(grant_state, seed=7)
    grant_engine.suppress_skills(0, YIJUE)
    assert LONGDAN not in _instances(grant_state, 0), (
        "非装备的虚拟授予不是装备技能，仍受失效覆盖"
    )


def test_an_unrecognized_source_is_not_suppressed_fail_closed():
    """来源判断不了 ⇒ 不失效（fail-closed），不把未知实例误锁掉。"""
    from sgs.skills import CATALOG
    from sgs.skills.catalog import SkillInstance, SkillSource, _skill_is_suppressed
    from sgs.state import SkillSuppression

    engine = _engine(("guanyu",))
    player = engine.state.player(0)
    player.suppressed_skills.append(SkillSuppression(skill_id=YIJUE))
    instance = SkillInstance(
        owner=0,
        skill_id=WUSHENG,
        source=SkillSource(kind="UNRECOGNIZED"),
        definition=CATALOG[WUSHENG],
    )

    assert _skill_is_suppressed(player, instance) is False


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
