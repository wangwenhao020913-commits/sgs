"""NPC 技能 4 条（M7 票 27；票 02 §7）。

NPC 技能**只授敌方座位**：它们是敌人模板的"武将牌技能"（模板 ``initial_skills`` /
``random{pool,count}`` 经 ``PveConfig.grants`` 进 enemy 座位的 ``GRANTED`` 来源），
**不进玩家学习池 / 价值表 / 商店 / 三选一**。载体 = ``CarrierKind.NPC``（第六档）：
它把"这条技能不属于任何武将牌、也不属于玩家成长面"变成**结构事实**——不是 id 前缀，
也不是稀有度。

形状（`docs/agents/skill-authoring/shapes.md` §2）：

| 技能 | 效果 | 形状 / 挂点 |
|---|---|---|
| ``npc.fury`` | 造成伤害 +1 | 纯修饰器 ``DAMAGE_DEAL_AMOUNT``（样板【古锭刀】/ 遗物 ``relic.damage_dealt``） |
| ``npc.guard`` | 受到伤害 -1 | 纯修饰器 ``DAMAGE_TAKE_AMOUNT``（样板【藤甲】的目标侧链） |
| ``npc.swift`` | 与其他角色的距离 -1 | 纯修饰器 ``DISTANCE``（样板【马术】，**主体侧**） |
| ``npc.wrath`` | 造成伤害后回复 1 点体力 | 造伤后钩子 ``DAMAGE_AFTER_DEAL`` + ``RECOVER`` 子帧（样板【狂骨】②的简化） |

**没有新增询问形态 / DOM**（C3）：四条全是锁定技，AI 一句话都不用答。
"""

from __future__ import annotations

import pytest

from sgs.data import (
    Identity,
    Mode,
    Rarity,
    RuleConfig,
    SkillValue,
    SkillValueTable,
    load_content_pack,
)
from sgs.effects import CarrierKind, damage_amount, distance
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.skills.npcs import (
    NPC_FURY,
    NPC_GUARD,
    NPC_SKILL_IDS,
    NPC_SWIFT,
    NPC_WRATH,
)
from sgs.state import FrameKind, Phase
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, cards_of_type, run_queries

PACK = load_content_pack(deck_profile="standard_108")

FURY, GUARD, SWIFT, WRATH = NPC_FURY, NPC_GUARD, NPC_SWIFT, NPC_WRATH


def _state(*npc_skills: str, seats: int = 2, enemy: int | None = None):
    """``seats`` 座；``npc_skills`` 写进**座位 1**（或 ``enemy``）的获得来源。

    NPC 技能的唯一入口就是 ``Player.granted_skills``（``GRANTED`` 来源）——本文件直接写它，
    与 ``PveConfig.grants`` 开局写入的那一笔逐字同形（那条路由下面的 PvE 局测试覆盖）。
    """
    seat = 1 if enemy is None else enemy
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4) for _ in range(seats)],
        deck=tuple(range(60, 108)),
    )
    state.player(seat).granted_skills.extend(npc_skills)
    return state


def _card(state):
    """一张实体【杀】：伤害链的渠道牌（从牌面取，不必先在谁手里）。"""
    return state.card(cards_of_type(PACK, "slash")[0])


# ---------------------------------------------------------------------------
# 一、``npc.fury``：造成伤害 +1（``DAMAGE_DEAL_AMOUNT``，只属于持有者）
# ---------------------------------------------------------------------------


def test_the_fury_npc_skill_adds_one_point_of_damage():
    """敌人造成的伤害 = 基础 + 1；**反例两半**：没有这条技能 ⇒ 1；加成不归别人。

    走 :func:`~sgs.effects.damage_amount` 的**来源侧**链（``chain=False``，伤害帧唯一调用点）。
    """
    plain = _state()
    boosted = _state(FURY)
    card = _card(plain)
    assert card is not None

    assert damage_amount(plain, source=1, target=0, card=card, base=1, chain=False) == 1, (
        "反例：没有 npc.fury ⇒ 基础 1 点"
    )
    assert damage_amount(boosted, source=1, target=0, card=card, base=1, chain=False) == 2, (
        "持有 npc.fury 的敌人造成伤害 +1"
    )
    assert damage_amount(boosted, source=0, target=1, card=card, base=1, chain=False) == 1, (
        "反例：加成只属于持有者（座位 0 造成的伤害不变）"
    )


# ---------------------------------------------------------------------------
# 二、``npc.guard``：受到伤害 -1（``DAMAGE_TAKE_AMOUNT``，只属于持有者、不是抵满）
# ---------------------------------------------------------------------------


def test_the_guard_npc_skill_absorbs_one_point_of_damage():
    """敌人受到的伤害 = 基础 − 1（下限 0）；**反例两半**：没有这条 ⇒ 1；别的座位照样吃满。

    走目标侧链（``DAMAGE_TAKE_AMOUNT``，每帧派发）；``base=3`` 那一半证明它是"减 1"而不是
    "伤害最多 1 点"（后者是 ``DAMAGE_CAP`` 挂点的【白银狮子】）。
    """
    plain = _state(seats=3)
    guarded = _state(GUARD, seats=3)

    assert damage_amount(plain, source=0, target=1, card=None, base=1, chain=False) == 1, (
        "反例：没有 npc.guard ⇒ 基础 1 点"
    )
    assert damage_amount(guarded, source=0, target=1, card=None, base=1, chain=False) == 0, (
        "持有 npc.guard 的敌人受到 1 点伤害 ⇒ 0（下限仍是 0）"
    )
    assert damage_amount(guarded, source=0, target=2, card=None, base=1, chain=False) == 1, (
        "反例：只对持有者生效（座位 2 照样吃 1 点）"
    )
    assert damage_amount(guarded, source=0, target=1, card=None, base=3, chain=False) == 2, (
        "3 点 → 2 点：是减 1，不是『伤害最多 1 点』"
    )


# ---------------------------------------------------------------------------
# 三、真实 PvE 局：模板技能经编译进 header、只落敌方座位、在局里真的生效
# ---------------------------------------------------------------------------


def _npc_library(*, with_npc_skills: bool):
    """手搓剧本库（``support.script_mode``）的两条敌人模板带 NPC 技能 / 不带。

    这是**剧本内容**的接线：模板 ``initial_skills`` 是票 28 的内容落点，本票只证明"模板带
    npc.* ⇒ 编译成 enemy 座位的 grants ⇒ 局里生效"这条链是通的。
    """
    from support import script_mode

    base = script_mode.library()
    if not with_npc_skills:
        return base, script_mode
    templates = tuple(
        template.model_copy(
            update={
                "initial_skills": {
                    "tpl.grunt": (SWIFT,),
                    "tpl.brute": (GUARD,),
                }[template.id],
                "random": _empty_random_skills(),
            }
        )
        if template.id in ("tpl.grunt", "tpl.brute")
        else template
        for template in base.templates
    )
    return base.model_copy(update={"templates": templates}), script_mode


def _empty_random_skills():
    from sgs.data import RandomSkills

    return RandomSkills(pool="pool.empty", count=(0, 0))


def _compiled_pve(*, with_npc_skills: bool):
    from sgs.script import compile_battle

    lib, script_mode = _npc_library(with_npc_skills=with_npc_skills)
    layer = script_mode.probe_layer()
    room = next(room for room in lib.rooms if room.id == "room.story")
    node = next(node for node in layer.nodes if node.id == "entry")
    return compile_battle(
        room=room, node=node, layer=layer, library=lib, run=script_mode.run_state()
    )


def _pve_engine(compiled) -> Engine:
    """用编译出来的 header 起一局（PvE 的**唯一**开局入口，票 09 / 10）。"""
    rule = RuleConfig(
        player_count=len(compiled.pve.identities),
        mode=Mode.PVE,
        pve=compiled.pve,
    )
    return Engine.new_game(seed=compiled.battle_seed, rule_config=rule, pack=PACK)


def _started(engine: Engine, seats: int) -> Engine:
    """推到第 1 个回合之前（``GAME_START`` 已派发、还没人摸牌）。"""
    from support import ran_timing

    return run_queries(
        engine,
        [PassPlayer() for _ in range(seats)],
        lambda e: ran_timing(e, Timing.TURN_BEFORE_START),
    )


def _resolve_damage(engine: Engine) -> Engine:
    """推到 ``DAMAGE_END``（这条伤害帧走完：含它压出来的回复 / 濒死子帧）。"""
    for _ in range(20_000):
        if any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in engine.records
        ):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        engine.step_default()
    raise AssertionError("步数上限内没有走完这条伤害帧")


def _damage_once(engine: Engine, *, source: int, target: int, amount: int = 1) -> Engine:
    """压一条与引擎在【杀】命中时同形的 ``DAMAGE`` 子帧，再推到 ``DAMAGE_END``。

    ``channel`` 取来源座位的首张手牌（没手牌时退到牌堆顶那张）：伤害链只读它的牌面，
    与引擎在【杀】命中时压的形状逐字同形（先例 ``tests/test_armor.py``）。
    """
    hand = engine.state.players[source].hand
    channel = engine.state.card(hand[0] if hand else engine.state.deck[0])
    frame = {
        "source": source,
        "targets": (target,),
        "card": channel,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": target,
        "channel": channel,
        "nature": "NORMAL",
        "chain": False,
        "amount": amount,
    }
    engine.push_frame(FrameKind.DAMAGE, frame, timing=Timing.DAMAGE_DEAL)
    for _ in range(20_000):
        if any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in engine.records
        ):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        engine.step_default()
    raise AssertionError("步数上限内没有走完这条伤害帧")


def test_a_compiled_pve_battle_grants_the_npc_skills_to_the_enemy_seats_only():
    """真实 PvE 局：两条 NPC 技能经模板 → 编译 → ``grants`` → enemy 座位，且**局里生效**。

    四个座位 = 主角（0）/ 队友（1）/ 小怪（2）/ Boss（3）。四条断言各钉一件事：

    1. **只落敌方**：座位 0 / 1（ally）一条 npc.* 都没有，座位 2 拿 ``npc.swift``、
       座位 3 拿 ``npc.guard``；
    2. **能解析**：两个实例的来源是 ``GRANTED``（既有技能入口，不新造来源）；
    3. **距离真的短了**（``npc.swift``）：敌人算主角 = 2 − 1 = 1；反方向（主角算敌人）仍是 2，
       别的座位对也不受影响；
    4. **伤害真的被吃掉 1 点**（``npc.guard``）：同一个伤害帧落在 Boss 身上 = 0 点，落在
       没有这条技能的小怪身上 = 1 点。
    """
    from sgs.skills import SkillSourceKind, resolve_instances

    compiled = _compiled_pve(with_npc_skills=True)
    pve = compiled.pve
    assert [identity for identity in pve.identities] == [
        Identity.ALLY,
        Identity.ALLY,
        Identity.ENEMY,
        Identity.ENEMY,
    ], "前置：主角 + 队友 + 两只敌人（剧本房间的编排）"

    engine = _pve_engine(compiled)
    state = engine.state

    # 1. 只落敌方座位。
    assert not set(state.player(0).granted_skills) & set(NPC_SKILL_IDS), (
        "主角一条 NPC 技能都没有"
    )
    assert not set(state.player(1).granted_skills) & set(NPC_SKILL_IDS), (
        "AI 队友（ally）也不拿 NPC 技能（它自己那条武将技照常带着，见下）"
    )
    assert list(state.player(1).granted_skills) == ["hero.liubei.jijiang"], (
        "队友模板的武将技不受影响：NPC 的座位角色门只管 npc.* 那几条"
    )
    assert list(state.player(2).granted_skills) == [SWIFT], "小怪模板的 npc.swift 落到敌方座位"
    assert list(state.player(3).granted_skills) == [GUARD], "Boss 模板的 npc.guard 落到敌方座位"

    # 2. 实例来自既有的「获得」来源。
    granted = {
        (instance.owner, instance.skill_id): instance
        for instance in resolve_instances(state)
        if instance.skill_id in NPC_SKILL_IDS
    }
    assert set(granted) == {(2, SWIFT), (3, GUARD)}, "只有两个敌方座位带着 NPC 技能实例"
    assert all(
        instance.source.kind is SkillSourceKind.GRANTED for instance in granted.values()
    ), "NPC 技能走既有技能入口（GRANTED），不新造来源"

    # 3. 距离缩短（主体侧；反方向与别的座位对不受影响）。
    assert distance(state, 2, 0) == 1, "小怪算主角：座位距离 2 − 1"
    assert distance(state, 0, 2) == 2, "反方向不减（牌面是『你与其他角色的距离』）"
    assert distance(state, 1, 3) == 2, "没有 NPC 距离技能的座位对不受影响"

    # 4. 伤害链上真的吃掉 1 点（同一个伤害帧：Boss 0 点、小怪 1 点）。
    _started(engine, seats=4)
    _damage_once(engine, source=0, target=3)
    assert state.player(3).hp == 4, "Boss 带 npc.guard：1 点伤害被减到 0（体力不动）"
    _damage_once(engine, source=0, target=2)
    assert state.player(2).hp == 3, "小怪没有 npc.guard：照常吃 1 点"


def test_the_real_pve_control_game_without_npc_skills_takes_the_full_damage():
    """反例：同一间房 / 同一份编译，模板**不带** NPC 技能 ⇒ 距离 2、Boss 照常掉 1 点。"""
    compiled = _compiled_pve(with_npc_skills=False)
    engine = _pve_engine(compiled)
    state = engine.state

    assert distance(state, 2, 0) == 2, "反例：没有 npc.swift ⇒ 座位距离 2"
    _started(engine, seats=4)
    _damage_once(engine, source=0, target=3)
    assert state.player(3).hp == 3, "反例：没有 npc.guard ⇒ 照常掉 1 点"


# ---------------------------------------------------------------------------
# 四、``npc.swift``：与其他角色的距离 -1（``DISTANCE``，**主体侧**）
# ---------------------------------------------------------------------------


def test_the_swift_npc_skill_shortens_its_owners_distance():
    """敌人算别人 −1（五座：座位 1 与 4 的座位距离 2 ⇒ 1）；反方向与别的座位对不变。

    **只做主体侧**是刻意的读法（牌面「你与其他角色的距离」，与【马术】同向）；遗物
    ``relic.distance`` 的"双向"是另一条技能的读法，两者不能混。
    """
    def _seats(*npc_skills: str):
        return _state(*npc_skills, seats=5, enemy=1)

    plain = _seats()
    swift = _seats(SWIFT)

    assert distance(plain, 1, 4) == 2, "反例：没有 npc.swift ⇒ 座位距离 2"
    assert distance(swift, 1, 4) == 1, "持有 npc.swift：敌人算别人 −1"
    assert distance(swift, 4, 1) == 2, "反方向不减（主体侧）"
    assert distance(swift, 2, 4) == 2, "别的座位对不受影响（逐座位）"
    assert distance(swift, 1, 2) == 1, "相邻座位已经是下限 1：不会减到 0"


# ---------------------------------------------------------------------------
# 五、``npc.wrath``（Boss）：造成伤害后回复 1 点体力（``DAMAGE_AFTER_DEAL`` + RECOVER 子帧）
# ---------------------------------------------------------------------------


def _boss_engine(*npc_skills: str, seat: int = 1) -> Engine:
    """座位 0 = 4/4，座位 1 = 2/4 的 Boss（带 ``npc_skills``）；从准备阶段起跑。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=2, hp_max=4)],
        deck=tuple(range(60, 108)),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    state.player(seat).granted_skills.extend(npc_skills)
    return Engine.from_state(state, seed=2701)


def test_the_wrath_boss_heals_one_point_after_it_deals_damage():
    """Boss 造成 2 点伤害后回复 **1** 点（逐事件、不逐点）；逐条核三个反例。

    - 没有这条技能 ⇒ 不回复；
    - 伤害**不是**它造成的（别人打别人）⇒ 不回复（"关于我"的自校）；
    - 满体力时什么也不发生（不压回复帧，规则 §4.15「满体力时不回复」）。
    """
    plain = _boss_engine()
    _damage_once(plain, source=1, target=0, amount=2)
    assert plain.state.player(1).hp == 2, "反例：没有 npc.wrath ⇒ Boss 不回复"

    blessed = _boss_engine(WRATH)
    _damage_once(blessed, source=1, target=0, amount=2)
    assert blessed.state.player(1).hp == 3, (
        "造成 2 点伤害 ⇒ 回 1 点（逐事件：牌面没有『1 点』限定词）"
    )
    assert blessed.state.player(0).hp == 2, "前置：这 2 点伤害真的落到了座位 0 身上"

    bystander = _boss_engine(WRATH)
    _damage_once(bystander, source=0, target=1, amount=1)
    assert bystander.state.player(1).hp == 1, (
        "反例：伤害不是它造成的 ⇒ 不回复（别人打它只掉血）"
    )

    full_hp = _boss_engine(WRATH)
    full_hp.state.players[1].hp = 4
    _damage_once(full_hp, source=1, target=0, amount=1)
    assert full_hp.state.player(1).hp == 4, "满体力 ⇒ 不回复（也不落回复记录）"


# ---------------------------------------------------------------------------
# 六、成长面：不进价值表、不进学习池 / 商店 / 三选一（**入口过滤**，不是稀有度）
# ---------------------------------------------------------------------------


def test_the_npc_skills_are_not_on_any_hero_card_and_not_in_the_value_table():
    """四条 id 既不在任何武将牌的技能清单里，也不在两段价值表里（票 02 §7）。"""
    from sgs.data import hero_skill_ids, load_hero_tables, load_skill_values

    on_hero_cards: set[str] = set()
    for hero_table in load_hero_tables():
        for hero in hero_table.heroes:
            on_hero_cards.update(hero_skill_ids(hero.id))

    table = load_skill_values()
    assert not set(NPC_SKILL_IDS) & on_hero_cards, (
        "NPC 技能不是武将技能：id 不许出现在任何武将牌的技能清单里"
    )
    assert not set(NPC_SKILL_IDS) & (set(table.skills) | set(table.relics)), (
        "NPC 技能不进价值表：两段都不收"
    )


def _enabled_npc_table() -> SkillValueTable:
    """一张**把 npc.fury 塞进已启用清单**的价值表（绕过 loader 的语义门，专测入口过滤）。

    判据是"即便表说 enabled，池子也不产出它"——这条防线不靠稀有度，也不靠 loader：
    ``npc.fury`` 的稀有度刻意给最高档（传说），漏进池就会立刻被抽到。
    """
    return SkillValueTable(
        schema_version=1,
        skills={
            "hero.guanyu.wusheng": SkillValue(enabled=True, rarity=Rarity.COMMON),
            FURY: SkillValue(enabled=True, rarity=Rarity.LEGENDARY),
        },
        relics={},
    )


def test_the_npc_skills_never_enter_the_learning_pool_shop_or_three_choice():
    """把 npc.* 塞进价值表启用清单 ⇒ 学习池 / 商店 / 三选一**仍然不产出它**。

    三处读的是同一个入口（``learning_pool`` = 表里已启用 − 已拥有），故一个断言三个面：
    ``learning_pool``（学习池） / ``shop_stock``（商店 6 技能） / ``draw_candidates``（三选一）。
    """
    from sgs.data import learning_pool
    from sgs.script import draw_candidates, shop_stock
    from sgs.script.rewards import clean_pool
    from support import script_mode

    table = _enabled_npc_table()
    assert table.enabled_skill_ids() == ("hero.guanyu.wusheng", FURY), (
        "前置：这张表确实把 npc.fury 点成了 enabled"
    )

    assert learning_pool(table) == ("hero.guanyu.wusheng",), (
        "学习池按**载体**排除 NPC 技能（入口过滤，不看稀有度）"
    )

    run = script_mode.run_state()
    assert clean_pool(run, table) == ("hero.guanyu.wusheng",), "商店读的学习池同样干净"

    skills, relics = shop_stock(
        run=run, value_table=table, seed=script_mode.SEED, node_key="np01"
    )
    assert FURY not in skills, "商店 6 技能的货架上不会出现 NPC 技能"
    assert relics == (), "这张表没有遗物段"

    rounds = draw_candidates(
        sizes=(3,),
        pool=clean_pool(run, table),
        value_table=table,
        seed=script_mode.SEED,
        node_key="np02",
    )
    drawn = {skill_id for row in rounds for skill_id in row}
    assert FURY not in drawn, "三选一（奖励候选）不会抽到 NPC 技能"
    assert drawn == {"hero.guanyu.wusheng"}, "池里只剩那一条武将技能"


def test_the_value_table_loader_refuses_an_npc_skill_in_either_section():
    """文件级防线（纵深）：价值表两段都拒 NPC id，不用等池子过滤。

    ``skills`` 段按"武将牌上已实现的技能"判 ⇒ ``skill_values.unknown_skill``；
    ``relics`` 段按"载体为 RELIC"判 ⇒ ``skill_values.not_a_relic``。
    """
    from sgs.data import DataLoadError, validate_skill_values

    hero_section = {
        "schema_version": 1,
        "skills": {FURY: {"enabled": True, "rarity": "common"}},
        "relics": {},
    }
    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(hero_section)
    assert [error.rule_id for error in caught.value.errors] == ["skill_values.unknown_skill"]

    relic_section = {
        "schema_version": 1,
        "skills": {},
        "relics": {FURY: True},
    }
    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(relic_section)
    assert [error.rule_id for error in caught.value.errors] == ["skill_values.not_a_relic"]


# ---------------------------------------------------------------------------
# 七、C3：不新增询问形态 / DOM
# ---------------------------------------------------------------------------


def test_the_npc_skills_introduce_no_new_query_shape_and_no_dom():
    """形状封闭：四条全是锁定技、无流水线 / 接缝 / 排列 / 显示组件；一局里零条发动询问。

    运行面 = 一局四条 NPC 技能都在场的真局（伤害帧走完整条链），断言**一条
    ``SKILL_ACTIVATION`` 询问都没有**：锁定技不问"是否发动"，这正是"AI 读点不新增"的判据。
    """
    for skill_id in NPC_SKILL_IDS:
        definition = CATALOG[skill_id]
        assert definition.flow is None, skill_id
        assert definition.seams == (), skill_id
        assert definition.deck_arrange is None, skill_id
        assert definition.virtual_slot is None, skill_id
        assert definition.appearance is None, skill_id
        assert definition.fixed_hand_count is None, skill_id
        assert definition.step_requirements is None, skill_id

    engine = _boss_engine(*NPC_SKILL_IDS)
    _damage_once(engine, source=1, target=0, amount=2)

    activations = [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SKILL_ACTIVATION"
    ]
    assert not activations, "四条 NPC 技能都是锁定效果：不产生『是否发动』询问"

# ---------------------------------------------------------------------------
# 八、四条技能的家族面：载体 = NPC、锁定、零询问、不在武将牌上
# ---------------------------------------------------------------------------


def test_the_npc_family_is_a_carrier_off_the_hero_cards():
    """四条 id 各一条 ``CATALOG`` 定义：载体 ``NPC``、锁定、没有流水线 / 接缝。

    ``NPC`` 是**第六种载体**（票 27 的框架加法）：它同时把"不进玩家成长面"变成结构事实
    ——``learning_pool`` 按载体排除（见 §五），``PveConfig.grants`` 按载体拒非敌方座位
    （见 ``tests/test_rule_config.py``）。
    """
    from sgs.skills import npc_skill_ids

    npc_carrier = {
        skill_id
        for skill_id, definition in CATALOG.items()
        if definition.carrier is CarrierKind.NPC
    }
    assert npc_carrier == set(NPC_SKILL_IDS)
    assert set(NPC_SKILL_IDS) == {FURY, GUARD, SWIFT, WRATH}
    assert npc_skill_ids() == set(NPC_SKILL_IDS), (
        "两处机械门（学习池入口排除 / grants 座位角色门）读的是同一个派生函数"
    )

    for skill_id in NPC_SKILL_IDS:
        definition = CATALOG[skill_id]
        assert definition.carrier is CarrierKind.NPC, skill_id
        assert definition.locked is True, (
            f"{skill_id}: 敌人不选技能 ⇒ 锁定（不问『是否发动』，AI 读点不新增）"
        )
        assert definition.flow is None, skill_id
        assert definition.seams == (), skill_id
        assert definition.requirement, f"{skill_id} 缺一句人话（提示条 / 描述读它）"
