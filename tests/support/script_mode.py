"""M7 剧本模式（票 17）测试的共用进料：手搓的剧本库 + 价值表 + ``RunState``。

**不读内置剧本**（它会随票 28 变成真正的内容）：这一层判的是「编译 / 抽完 / 结算」的机械
关系，进料必须薄到一眼能核对。

三张表各有用途：

- :func:`library` —— 覆盖五类房间 + 两种敌人来源 + 大节曲线 + 加权问号池 + 两个剧本
  （``probe`` 主线、``deadend`` 死路、``plainboss`` 无数据奖励的 Boss）；
- :func:`value_table` —— 6 条已启用武将技能（四档稀有度）+ 1 条禁用技能 + 2 件遗物；
- :func:`run_state` —— 一份合法的 S₀（背包 24 格、可指定已持有技能 / 遗物 / 铜钱 / 进度）。

两条进料口径：

1. **技能 id 一律取真实 ``CATALOG`` 里的武将技能**：``PveConfig.grants`` 只收 ``CATALOG``
   的 id，编出来的 header 因此真能造。
2. **遗物用 ``equip.*`` 的 id 当合成遗物**：遗物技能的载体（票 26）还没落地，而
   ``PveConfig`` 只核 id ∈ ``CATALOG``、不核载体——这里测的是「``run.relics`` 经 ``grants``
   进 header」那段接线，不是载体语义。
"""

from __future__ import annotations

from typing import Any

from sgs.data import (
    Battle,
    CoinsEffect,
    DrawSpec,
    Edge,
    EnemyDraw,
    Event,
    EventOption,
    EventPool,
    GlobalSkill,
    GlobalSkillRef,
    GlobalSkillSide,
    GrantRelicEffect,
    GrantSkillEffect,
    Grid,
    Layer,
    Node,
    PveVictory,
    PveVictoryKind,
    QuestionEntry,
    RandomSkills,
    Rarity,
    Requirement,
    Reward,
    Room,
    RoomClear,
    RoomKind,
    Scale,
    Scaling,
    Script,
    ScriptLibrary,
    Seat,
    SeatRole,
    SetFlagsEffect,
    SkillPool,
    SkillValue,
    SkillValueTable,
    Template,
)
from sgs.script import (
    BACKPACK_SLOTS,
    Backpack,
    NodeStatus,
    RunState,
    progress_key,
)

SEED = 0x5EED_2026
"""默认 run seed（``[0, 2**64)`` 内的任意常数；所有可复现断言都以它为起点）。"""

SKILL_A = "hero.liubei.rende"
SKILL_B = "hero.guanyu.wusheng"
SKILL_C = "hero.zhangfei.paoxiao"
SKILL_D = "hero.zhaoyun.longdan"
SKILL_E = "hero.liubei.jijiang"
SKILL_F = "hero.jie_liubei.rende"
SKILL_G = "hero.caocao.jianxiong"
SKILL_H = "hero.sunquan.zhiheng"
"""七条真实武将技能（``CATALOG`` 里都有）；``SKILL_F`` 在价值表里**禁用**。

已启用 7 条 ⇒ 商店能真卖满 6 格、Boss 的两次三选一 + extra 的 pick1 都拿得到候选。
"""

RELIC_X = "equip.crossbow"
RELIC_Y = "equip.qinggang_sword"
"""两件**合成**遗物（真正的遗物技能随票 26 落地；见模块文档第 2 条）。"""

HERO = "jie_liubei"
"""默认选定的初始角色（4/4、蜀、男：体力字段来自武将表）。"""

LAYER_ID = "l1"
"""主线大节的 id（进度键的前缀）。"""


def edge(source: str, target: str, requires: Requirement | None = None) -> Edge:
    """一条边（``from`` 是 Python 关键字，故走别名）。"""
    return Edge(**{"from": source, "to": target, "requires": requires or Requirement()})


# ---------------------------------------------------------------------------
# 价值表：6 条已启用（四档稀有度）+ 1 条禁用 + 2 件遗物
# ---------------------------------------------------------------------------


def value_table() -> SkillValueTable:
    """一张小表：四档稀有度各有实例、有一条禁用、两件遗物。

    价与权重**不落表**（票 02 §4）：这里只给启用与否 + 稀有度，价格从
    ``sgs.data.RARITY_PRICES`` 现取。
    """
    return SkillValueTable(
        schema_version=1,
        skills={
            SKILL_A: SkillValue(enabled=True, rarity=Rarity.COMMON),
            SKILL_B: SkillValue(enabled=True, rarity=Rarity.RARE),
            SKILL_C: SkillValue(enabled=True, rarity=Rarity.EPIC),
            SKILL_D: SkillValue(enabled=True, rarity=Rarity.LEGENDARY),
            SKILL_E: SkillValue(enabled=True, rarity=Rarity.COMMON),
            SKILL_G: SkillValue(enabled=True, rarity=Rarity.RARE),
            SKILL_H: SkillValue(enabled=True, rarity=Rarity.COMMON),
            SKILL_F: SkillValue(enabled=False, rarity=Rarity.COMMON),
        },
        relics={RELIC_X: True, RELIC_Y: True},
    )


def other_hero_skill_ids(count: int = BACKPACK_SLOTS) -> tuple[str, ...]:
    """``count`` 条真实武将技能，**与本文件价值表里那 7 条不相交**（填满背包用）。

    装满包的三选一必须仍能抽到候选（池子空了会走"折 3 铜钱"那条），故这里刻意避开
    ``SKILL_A`` … ``SKILL_H``。
    """
    from sgs.skills import CATALOG

    known = {SKILL_A, SKILL_B, SKILL_C, SKILL_D, SKILL_E, SKILL_F, SKILL_G, SKILL_H}
    ids = tuple(
        skill_id
        for skill_id, definition in sorted(CATALOG.items())
        if definition.carrier.value == "HERO" and skill_id not in known
    )
    assert len(ids) >= count, "前置：武将技能够填满背包"
    return ids[:count]


# ---------------------------------------------------------------------------
# 剧本库：五类房间 + 两种敌人来源 + 大节曲线 + 加权问号池
# ---------------------------------------------------------------------------


def templates() -> tuple[Template, ...]:
    """友方模板 1 条 + 同档敌人 2 条（抽得开）+ 一条 Boss 模板。"""
    return (
        Template(
            id="tpl.ally",
            name="义军什长",
            band="ally",
            hp=3,
            hp_max=3,
            initial_skills=(SKILL_E,),
            random=RandomSkills(pool="pool.empty"),
            reward=Reward(coins=9),
        ),
        Template(
            id="tpl.grunt",
            name="黄巾力士",
            band="early",
            hp=3,
            hp_max=3,
            initial_skills=(),
            random=RandomSkills(pool="pool.grunts", count=(1, 2)),
            reward=Reward(coins=2),
        ),
        Template(
            id="tpl.brute",
            name="黄巾渠帅",
            band="early",
            hp=4,
            hp_max=4,
            initial_skills=(SKILL_C,),
            random=RandomSkills(pool="pool.empty"),
            reward=Reward(coins=3, pick3=1),
        ),
        Template(
            id="tpl.boss",
            name="张角",
            band="late",
            hp=8,
            hp_max=8,
            initial_skills=(SKILL_D,),
            random=RandomSkills(pool="pool.empty"),
            reward=Reward(coins=5),
        ),
    )


def skill_pools() -> tuple[SkillPool, ...]:
    return (
        SkillPool(id="pool.empty", name="空池", skills=()),
        SkillPool(id="pool.grunts", name="小怪技能", skills=(SKILL_A, SKILL_B, SKILL_D)),
        SkillPool(id="pool.extra", name="备用", skills=(SKILL_B,)),
    )


def global_skills() -> tuple[GlobalSkill, ...]:
    """两条副本全局技能：一条对敌、一条对友（测「只缩放它点名的那一侧」）。"""
    return (
        GlobalSkill(
            id="gs.enemy",
            name="黄巾之众（敌）",
            side=GlobalSkillSide.ENEMY,
            scale=Scale(hp=(1, 2), draw=(0, 1), slash=(1, 3)),
        ),
        GlobalSkill(
            id="gs.ally",
            name="汉室气运（友）",
            side=GlobalSkillSide.ALLY,
            scale=Scale(hp=(2, 2), draw=(1, 1), slash=(0, 0)),
        ),
    )


def events() -> tuple[Event, ...]:
    """两条事件：一条纯奖励、一条带标签门（``requires``）与遗物授予。"""
    return (
        Event(
            id="ev.village",
            name="残破的村庄",
            text="村民捧出铜钱。",
            options=(
                EventOption(text="收下", effects=(CoinsEffect(kind="coins", amount=2),)),
                EventOption(
                    text="帮他们修寨",
                    requires=Requirement(flags=("flag.event_done",)),
                    effects=(
                        SetFlagsEffect(kind="set_flags", flags=("flag.helped",)),
                        GrantSkillEffect(kind="grant_skill", skill=SKILL_B),
                        GrantRelicEffect(kind="grant_relic", relic=RELIC_Y),
                    ),
                ),
                EventOption(
                    text="重金修缮",
                    effects=(CoinsEffect(kind="coins", amount=-5),),
                ),
            ),
        ),
        Event(
            id="ev.ambush",
            name="道旁伏兵",
            text="树林里有人。",
            options=(EventOption(text="绕开", effects=(CoinsEffect(kind="coins", amount=-1),)),),
        ),
    )


def event_pools() -> tuple[EventPool, ...]:
    """两个池：``ep.one`` 有两条（抽事件的那条断言用它）、``ep.village`` 只有一条。"""
    return (
        EventPool(id="ep.one", name="事件池", events=("ev.village", "ev.ambush")),
        EventPool(id="ep.village", name="只剩村庄", events=("ev.village",)),
    )


def rooms() -> tuple[Room, ...]:
    """五类房间都在，且两种敌人来源各有实例。"""
    return (
        Room(
            id="room.story",
            name="颍川道上",
            kind=RoomKind.STORY,
            text="剧情战：一名受保护队友 + 两名固定敌人。",
            battle=Battle(
                allies=(Seat(role=SeatRole.ALLY, template="tpl.ally", protected=True),),
                enemies=(
                    Seat(role=SeatRole.ENEMY, template="tpl.grunt"),
                    Seat(role=SeatRole.ENEMY, template="tpl.brute"),
                ),
                victory=PveVictory(kind=PveVictoryKind.ROUT),
                global_skills=(GlobalSkillRef(skill="gs.enemy"),),
                extra_reward=Reward(coins=4, pick2=1),
            ),
        ),
        Room(
            id="room.draw",
            name="遭遇战",
            kind=RoomKind.BATTLE,
            text="按档抽敌人。",
            battle=Battle(
                enemies=EnemyDraw(draw=DrawSpec(band="early", count=(1, 2))),
                victory=PveVictory(kind=PveVictoryKind.ROUT),
                extra_reward=Reward(coins=1),
            ),
        ),
        Room(
            id="room.boss",
            name="广宗城下",
            kind=RoomKind.STORY,
            is_boss=True,
            text="Boss 战：先失去出战技能，再发奖。",
            battle=Battle(
                enemies=(Seat(role=SeatRole.ENEMY, template="tpl.boss", boss=True),),
                victory=PveVictory(kind=PveVictoryKind.SLAY_BOSS),
                extra_reward=Reward(coins=6, pick1=1),
            ),
        ),
        Room(
            id="room.boss.plain",
            name="无数据奖励的 Boss",
            kind=RoomKind.STORY,
            is_boss=True,
            text="数据一分钱不给：Boss 的固定奖励仍照发，失去那一段仍照走。",
            battle=Battle(
                enemies=(Seat(role=SeatRole.ENEMY, template="tpl.boss", boss=True),),
                victory=PveVictory(kind=PveVictoryKind.SLAY_BOSS),
            ),
        ),
        Room(
            id="room.ally_buff",
            name="友军增益",
            kind=RoomKind.BATTLE,
            text="副本全局技能只对友方生效（与 col 2 的大节曲线互斥：写了就覆盖）。",
            battle=Battle(
                allies=(Seat(role=SeatRole.ALLY, template="tpl.ally"),),
                enemies=(Seat(role=SeatRole.ENEMY, template="tpl.grunt"),),
                victory=PveVictory(kind=PveVictoryKind.ROUT),
                global_skills=(GlobalSkillRef(skill="gs.ally"),),
            ),
        ),
        Room(
            id="room.scale_override",
            name="区间覆盖",
            kind=RoomKind.BATTLE,
            text="引用的 scale 逐档覆盖定义里的区间（这里覆盖成全 0）。",
            battle=Battle(
                enemies=(Seat(role=SeatRole.ENEMY, template="tpl.grunt"),),
                victory=PveVictory(kind=PveVictoryKind.ROUT),
                global_skills=(
                    GlobalSkillRef(
                        skill="gs.enemy",
                        scale=Scale(hp=(0, 0), draw=(0, 0), slash=(0, 0)),
                    ),
                ),
            ),
        ),
        Room(
            id="room.event",
            name="村庄",
            kind=RoomKind.EVENT,
            event_pool="ep.one",
            on_clear=RoomClear(set_flags=("flag.event_done",)),
        ),
        Room(
            id="room.event.village",
            name="村庄（单条事件池）",
            kind=RoomKind.EVENT,
            event_pool="ep.village",
            on_clear=RoomClear(set_flags=("flag.event_done",)),
        ),
        Room(
            id="room.shop",
            name="行商",
            kind=RoomKind.SHOP,
            on_clear=RoomClear(set_flags=("flag.shop_done",)),
        ),
        Room(
            id="room.unknown",
            name="问号房（揭晓事件）",
            kind=RoomKind.UNKNOWN,
            pool=(QuestionEntry(room="room.event", weight=1),),
        ),
        Room(
            id="room.unknown.village",
            name="问号房（揭晓村庄）",
            kind=RoomKind.UNKNOWN,
            pool=(QuestionEntry(room="room.event.village", weight=1),),
        ),
        Room(
            id="room.unknown.battle",
            name="问号房（揭晓战斗）",
            kind=RoomKind.UNKNOWN,
            pool=(QuestionEntry(room="room.draw", weight=1),),
        ),
    )


def probe_layer() -> Layer:
    """主线大节：两个入口 + 分叉 + 一条标签门 + 一条大节曲线。

    ::

        entry(0,0) ─┬─> event(0,1) ─────────────> late(1,2) ─┬─> boss(3,2) ─┐
                    ├─> draw(1,1) ─┬─────────────> late      ├─> exit(0,3) ─┤
                    │              └─────────────> exit      │              │
                    └─> shop(2,1) ─[flag.shop_done]─────────┴──────────────┘
        unknown(2,0) ────────────────────────────> late
        unknown_battle(3,0) ─────────────────────> late
        unknown_village(1,0) ────────────────────> late

    两个入口（``entry`` / ``unknown*`` 都在 col 0）、多出口、一条标签门；
    ``late`` 与 ``boss`` 落在 col 2：大节曲线的第二段（``from_col = 2``）只对它们生效。
    ``boss`` 单独一格（col 2 ⇒ 不是出口）：结算 Boss 之后整轮还在，方便核对它的账。
    """
    nodes = (
        Node(id="entry", row=0, col=0, room="room.story"),
        Node(id="unknown", row=2, col=0, room="room.unknown"),
        Node(id="unknown_battle", row=3, col=0, room="room.unknown.battle"),
        Node(id="unknown_village", row=1, col=0, room="room.unknown.village"),
        Node(id="draw", row=1, col=1, room="room.draw"),
        Node(id="event", row=0, col=1, room="room.event"),
        Node(id="shop", row=2, col=1, room="room.shop"),
        Node(id="late", row=1, col=2, room="room.draw"),
        Node(id="boss", row=3, col=2, room="room.boss"),
        Node(id="exit", row=0, col=3, room="room.boss"),
    )
    edges = (
        edge("entry", "draw"),
        edge("entry", "event"),
        edge("entry", "shop"),
        edge("unknown", "late"),
        edge("unknown_battle", "late"),
        edge("unknown_village", "late"),
        edge("draw", "late"),
        edge("draw", "exit"),
        edge("event", "late"),
        edge("shop", "exit", Requirement(flags=("flag.shop_done",))),
        edge("late", "boss"),
        edge("late", "exit"),
        edge("boss", "exit"),
    )
    scaling = (
        Scaling(from_col=0, hp=(0, 0), draw=(0, 0), slash=(0, 0)),
        Scaling(from_col=2, hp=(1, 1), draw=(0, 0), slash=(2, 2)),
    )
    return Layer(
        id=LAYER_ID,
        name="大节一",
        grid=Grid(rows=4, cols=4),
        nodes=nodes,
        edges=edges,
        default_scaling=scaling,
    )


def deadend_layer() -> Layer:
    """一条走不通的路线：唯一出边被标签挡住（运行期=死路，票 03 §2）。"""
    return Layer(
        id="d1",
        name="死路大节",
        grid=Grid(rows=1, cols=2),
        nodes=(
            Node(id="a", row=0, col=0, room="room.shop"),
            Node(id="b", row=0, col=1, room="room.shop"),
        ),
        edges=(edge("a", "b", Requirement(flags=("flag.never",))),),
    )


def plainboss_layer() -> Layer:
    """两格直线：商店 → 无数据奖励的 Boss（Boss 落在出口列）。"""
    return Layer(
        id="p1",
        name="无奖大节",
        grid=Grid(rows=1, cols=2),
        nodes=(
            Node(id="entry", row=0, col=0, room="room.shop"),
            Node(id="boss", row=0, col=1, room="room.boss.plain"),
        ),
        edges=(edge("entry", "boss"),),
    )


def scripts() -> tuple[Script, ...]:
    return (
        Script(
            id="probe",
            name="探针剧本",
            version=1,
            roster=("nengren", "jie_liubei", "jie_guanyu"),
            layers=(probe_layer(),),
        ),
        Script(
            id="deadend",
            name="死路剧本",
            version=1,
            roster=("nengren", "jie_liubei", "jie_guanyu"),
            layers=(deadend_layer(),),
        ),
        Script(
            id="plainboss",
            name="无奖 Boss 剧本",
            version=1,
            roster=("nengren", "jie_liubei", "jie_guanyu"),
            layers=(plainboss_layer(),),
        ),
    )


def library() -> ScriptLibrary:
    return ScriptLibrary(
        script_schema_version=1,
        scripts=scripts(),
        rooms=rooms(),
        templates=templates(),
        skill_pools=skill_pools(),
        events=events(),
        event_pools=event_pools(),
        global_skills=global_skills(),
    )


# ---------------------------------------------------------------------------
# RunState：一份合法的 S₀
# ---------------------------------------------------------------------------


def backpack(*skills: str) -> Backpack:
    """按顺序填格（前 4 格 = 出战槽）：``backpack(SKILL_A, SKILL_B)`` = 两条都在出战槽。"""
    assert len(skills) <= BACKPACK_SLOTS, "背包只有 24 格"
    slots: tuple[str | None, ...] = tuple(skills) + (None,) * (
        BACKPACK_SLOTS - len(skills)
    )
    return Backpack(slots=slots)


def run_state(**overrides: Any) -> RunState:
    """一份合法的 S₀；``overrides`` 可以改任何字段（测试用它摆出中间态）。"""
    fields: dict[str, Any] = {
        "run_id": f"{SEED}-1",
        "revision": 0,
        "seed": SEED,
        "script_id": "probe",
        "script_version": 1,
        "script_digest": "sha256:" + "0d" * 32,
        "roster_candidates": ("nengren", "jie_liubei", "jie_guanyu"),
        "chosen_hero": HERO,
        "layer_index": 0,
        "flags": (),
        "coins": 0,
        "backpack": backpack(),
        "relics": (),
        "node_status": {},
        "pending": None,
    }
    fields.update(overrides)
    return RunState(**fields)


def completed(layer_id: str, *node_ids: str) -> dict[str, NodeStatus]:
    """把若干节点标成 ``completed``（进度键由 :func:`progress_key` 拼）。"""
    return {progress_key(layer_id, node_id): NodeStatus.COMPLETED for node_id in node_ids}
