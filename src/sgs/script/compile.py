"""房间编译：``Room → 一局能起的 PvE`` + **进入节点 = 一次抽完**（M7 票 17）。

依据 **票 03 §3（座位 / 敌人来源 / 胜负 / 全局技能 / 大节曲线）**、**票 03 §5（子流）**
与 **票 04 §0 纪律 2（进入节点时把该节点的随机一次抽完）**。

两个入口共用一条随机流水线：

:func:`compile_battle`
    ``Room → PveConfig``。座位表（主角恒 0 号 / ``allies`` 0~2 / ``enemies`` 1~4）、阵营、
    保护对象、胜负、Boss 座位、授予（出战技能 + 遗物 + 每个 AI 座位的模板技能）、副本全局
    技能的缩放**全部落进 header**——同一份快照 + 同 seed 因此能自证出同一局。它同时抽出
    这一场的**种子**（``script.battle:<节点>`` 的第一个数）。

:func:`enter_node`
    ``节点 → RunState``：五类房间各自的随机**一次抽完**并物化进 ``pending.spec``，节点内
    不再取随机。进入前先算可到达集（:func:`sgs.script.available_next`）：算不出来就抛票 16
    的那个错误（:class:`~sgs.script.DeadEndError` / :class:`~sgs.script.ScriptError`），
    绝不静默软锁。

三条读法（票面没写死，本模块按此口径钉住，见票 17 的 ``## Answer``）：

1. **``<节点>`` = 进度键** ``"<layer_id>/<node_id>"``（与 ``pending.node_id`` 同形）：
   节点 id 是层内编号，两层各有 ``n1`` 是常态，用进度键才让子流在整轮里唯一。
2. **模板随机技能只抽一次**：``random.count`` 先抽个数，再在同一
   ``script.enemy:<节点>:<座位>`` 上无放回抽技能；``<座位>`` 是 **PveConfig 的绝对座位号**
   （0 号恒为主角）。
3. **大节曲线只缩放敌方**：``Layer.default_scaling`` 没有 ``side`` 字段，而它的用途是
   "越往后越强"（票 03 §3）⇒ 落到敌方；房间写了 ``global_skills`` 就**覆盖**它（不是
   相加），与票 03 §3 的"继承与覆盖"一致。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from sgs.data import (
    Battle,
    EnemyDraw,
    Event,
    GlobalSkillSide,
    GlobalSkillRef,
    Identity,
    Layer,
    Node,
    PveConfig,
    PveSeat,
    PveVictoryKind,
    Room,
    RoomKind,
    Scale,
    Scaling,
    ScriptLibrary,
    Seat,
    SeatRole,
    SkillGrant,
    SkillValueTable,
    Template,
    hero_by_id,
)
from sgs.engine import RandomSource

from . import lookup, rewards
from .board import available_next
from .errors import ScriptError
from .rng import (
    battle_stream,
    draw_stream,
    enemy_stream,
    event_stream,
    uniform_sample,
    unknown_stream,
    weighted_sample,
)
from .state import (
    BattleSpec,
    EventSpec,
    Id,
    Pending,
    PendingSpec,
    RevealedSpec,
    RunState,
    SEED_LIMIT,
    ShopSpec,
    ShopStock,
    UnknownSpec,
    equipped_skills,
    progress_key,
)

__all__ = [
    "CompiledBattle",
    "ResolvedScale",
    "compile_battle",
    "enter_node",
    "hero_seat",
    "resolve_scaling",
]

MAX_SKILL_GRANTS_PER_SEAT: Final = 4
"""每座位的技能授予上限（票 09 的校验矩阵）：模板技能超过它就编不出 header。"""

_MAX_SEAT_BONUS: Final = 9
"""座位三档加成的上界（与 ``sgs.data.PveSeat`` 的校验矩阵同一把尺）。"""


class _Frozen(BaseModel):
    """本模块模型的共同纪律：不可变、无多余字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ResolvedScale(_Frozen):
    """「对哪一侧、用哪三个区间」——房间引用与大节曲线合成后的同一种形状。"""

    side: GlobalSkillSide
    scale: Scale


class CompiledBattle(_Frozen):
    """一次编译的产物：能起的一局 + 它自己的种子 + 敌方模板（结算奖励要读）。

    ``pve`` 就是 **header 的自证材料**（票 01 §2）：座位 / 阵营 / 胜负 / 授予都在里面，
    录像重建同一场只需要它 + ``battle_seed``。
    """

    pve: PveConfig
    battle_seed: int = Field(ge=0, lt=SEED_LIMIT)
    enemy_templates: tuple[Id, ...]
    """敌方座位各自的模板 id（按敌方座位序）；"全队模板奖励累加"读它。"""


# ---------------------------------------------------------------------------
# 缩放：房间引用（逐档覆盖定义） / 大节曲线（按列继承）
# ---------------------------------------------------------------------------


def resolve_scaling(
    *,
    battle: Battle | None,
    layer: Layer,
    col: int,
    library: ScriptLibrary,
) -> tuple[ResolvedScale, ...]:
    """这一场要套哪些缩放：房间写了就用房间的，否则按列继承大节曲线。

    房间级（``battle.global_skills``）优先且**排他**：写了就不再叠加 ``default_scaling``
    （票 03 §3 的"继承与覆盖"）。大节曲线取「最后一段 ``from_col <= col``」，只缩放敌方。
    """
    if battle is not None and battle.global_skills:
        return tuple(
            _resolve_reference(reference, library) for reference in battle.global_skills
        )
    segment = _curve_at(layer, col)
    if segment is None:
        return ()
    return (
        ResolvedScale(
            side=GlobalSkillSide.ENEMY,
            scale=Scale(hp=segment.hp, draw=segment.draw, slash=segment.slash),
        ),
    )


def _curve_at(layer: Layer, col: int) -> Scaling | None:
    """大节曲线里 ``from_col <= col`` 的**最后一段**（严格升序由加载器保证）。"""
    found = None
    for segment in layer.default_scaling:
        if segment.from_col <= col:
            found = segment
    return found


def _resolve_reference(
    reference: GlobalSkillRef, library: ScriptLibrary
) -> ResolvedScale:
    """一条 ``GlobalSkillRef``：定义的 ``side``/区间 + 引用处逐档覆盖。"""
    definition = lookup.global_skill_of(library, reference.skill)
    scale = definition.scale
    if reference.scale is not None:
        merged = {
            name: (
                getattr(reference.scale, name)
                if getattr(reference.scale, name) is not None
                else getattr(definition.scale, name)
            )
            for name in Scale.model_fields
        }
        scale = Scale(**merged)
    return ResolvedScale(side=definition.side, scale=scale)


def _sides_for(side: GlobalSkillSide, identities: Sequence[Identity]) -> tuple[int, ...]:
    """``side`` 命中哪些座位（``all`` = 全部；否则按阵营）；座位号升序。"""
    if side is GlobalSkillSide.ALL:
        return tuple(range(len(identities)))
    wanted = Identity.ALLY if side is GlobalSkillSide.ALLY else Identity.ENEMY
    return tuple(
        seat for seat, identity in enumerate(identities) if identity is wanted
    )


def _scaled(seat: PveSeat, scale: Scale, stream: RandomSource) -> PveSeat:
    """对一座抽一次三档加成（区间缺省 = 这一档不缩放），加进座位常驻加成。"""
    added = {
        "hp_max_bonus": _draw(scale.hp, stream),
        "draw_bonus": _draw(scale.draw, stream),
        "slash_damage_bonus": _draw(scale.slash, stream),
    }
    scaled = {
        name: getattr(seat, name) + bonus for name, bonus in added.items()
    }
    for name, value in scaled.items():
        if value > _MAX_SEAT_BONUS:
            raise ScriptError(
                rule_id="script.compile.scale_out_of_range",
                loc=seat.name or (seat.hero or "<座位>"),
                reason=(
                    f"座位常驻加成 {name} = {value} 越过上限 {_MAX_SEAT_BONUS}："
                    "缩放区间由副本全局技能 / 大节曲线给出，数据写得太大"
                ),
            )
    return seat.model_copy(update=scaled)


def _draw(span: tuple[int, int] | None, stream: RandomSource) -> int:
    """从闭区间里抽一个整数；``None`` = 这一档不缩放（也不消耗随机数）。"""
    if span is None:
        return 0
    return stream.randint(span[0], span[1])


# ---------------------------------------------------------------------------
# 座位：主角 / 队友 / 敌人（固定或按档抽）
# ---------------------------------------------------------------------------


def hero_seat(hero_id: str) -> PveSeat:
    """主角座位 = 武将表里点名的那个武将（票 03 §3「主角恒为 0 号」）。

    体力 / 势力 / 性别全部取自**武将数据**（初始体力不写 = 满体力）：``PveSeat`` 因此是
    一张自足的合成座位，header 不必再去查武将表就能重建同一场。
    """
    hero = hero_by_id(hero_id)
    if hero is None:
        raise ScriptError(
            rule_id="script.compile.unknown_hero",
            loc=hero_id,
            reason=f"武将表里没有 {hero_id!r}：初始角色必须点名一个已登记的武将",
        )
    return PveSeat(
        hero=hero.id,
        name=hero.name,
        hp=hero.hp_max if hero.hp is None else hero.hp,
        hp_max=hero.hp_max,
        kingdom=hero.kingdom,
        gender=hero.gender,
    )


def _template_seat(template: Template) -> PveSeat:
    """敌人 / 队友模板 → 合成武将座位（``hero=None``：敌人不是武将表记录）。"""
    return PveSeat(hero=None, name=template.name, hp=template.hp, hp_max=template.hp_max)


def _template_skills(
    template: Template,
    *,
    library: ScriptLibrary,
    seed: int,
    node_key: str,
    seat: int,
) -> tuple[str, ...]:
    """一个 AI 座位的技能：模板 ``initial_skills`` + 随机池里抽到的。

    个数与内容都从这一座位自己的 ``script.enemy:<节点>:<座位>`` 取：同座重放同结果，
    加一个座位也不会平移别人的抽签位置。
    """
    stream = enemy_stream(seed, node_key, seat)
    low, high = template.random.count
    count = stream.randint(low, high)
    pool = lookup.skill_pool_of(library, template.random.pool)
    drawn = uniform_sample(pool=pool.skills, count=count, source=stream)
    skills = tuple(template.initial_skills) + drawn
    if len(skills) > MAX_SKILL_GRANTS_PER_SEAT:
        raise ScriptError(
            rule_id="script.compile.too_many_skills",
            loc=template.id,
            reason=(
                f"模板 {template.id!r} 的初始技能 + 随机技能 = {len(skills)} 条，"
                f"超过每座位上限 {MAX_SKILL_GRANTS_PER_SEAT}（票 09 的校验矩阵）"
            ),
        )
    return skills


def _fixed_enemies(battle: Battle, library: ScriptLibrary) -> tuple[tuple[Seat, Template], ...]:
    """固定的敌方座位（剧情战 / Boss / 精英编排）。"""
    assert not isinstance(battle.enemies, EnemyDraw)
    return tuple(
        (seat, lookup.template_of(library, _required_template(seat))) for seat in battle.enemies
    )


def _drawn_enemies(
    battle: Battle, *, library: ScriptLibrary, seed: int, node_key: str
) -> tuple[tuple[Seat, Template], ...]:
    """按档抽：同 ``band`` 内无放回抽 ``count`` 个（个数与名单都由 ``script.draw:<节点>`` 抽）。"""
    assert isinstance(battle.enemies, EnemyDraw)
    draw = battle.enemies.draw
    stream = draw_stream(seed, node_key)
    count = stream.randint(draw.count[0], draw.count[1])
    band = tuple(
        template.id for template in library.templates if template.band == draw.band
    )
    picked = uniform_sample(pool=band, count=count, source=stream)
    return tuple(
        (
            Seat(role=SeatRole.ENEMY, template=template_id),
            lookup.template_of(library, template_id),
        )
        for template_id in picked
    )


def _required_template(seat: Seat) -> str:
    if seat.template is None:
        raise ScriptError(
            rule_id="script.compile.seat_without_template",
            loc=f"{seat.role.value}/{seat.template}",
            reason="AI 座位必须点名一个敌人模板（票 03 §1：敌人不是武将表记录）",
        )
    return seat.template


# ---------------------------------------------------------------------------
# 编译：Room → PveConfig
# ---------------------------------------------------------------------------


def compile_battle(
    *,
    room: Room,
    node: Node,
    layer: Layer,
    library: ScriptLibrary,
    run: RunState,
) -> CompiledBattle:
    """把房间编成能起的一局（并抽出这一场的种子）。

    :raises ScriptError: 房间没有 ``battle`` 载荷 / run 已经在某个节点里 / 引用的模板、
        技能池、全局技能不在快照里 / 模板技能超过每座位上限 / 缩放越过加成上界。
    """
    battle = room.battle
    if battle is None:
        raise ScriptError(
            rule_id="script.compile.not_a_battle_room",
            loc=room.id,
            reason=(
                f"房间 {room.id!r} 的 kind = {room.kind.value}，没有 battle 载荷："
                "只有剧情 / 意外战斗（以及问号房揭晓出来的它们）可编译成 PveConfig"
            ),
        )
    if run.pending is not None:
        raise ScriptError(
            rule_id="script.enter.node_in_progress",
            loc=run.pending.node_id,
            reason="当前节点尚未结算：编译只发生在**进入节点**那一步（票 04 §2 的 S_enter）",
        )

    node_key = progress_key(layer.id, node.id)
    stream = battle_stream(run.seed, node_key)
    battle_seed = stream.randbelow(SEED_LIMIT)

    seats: list[PveSeat] = [hero_seat(run.chosen_hero)]
    identities: list[Identity] = [Identity.ALLY]
    skills: list[tuple[str, ...]] = [equipped_skills(run.backpack)]
    protected_ally: int | None = None
    boss: int | None = None

    for seat in battle.allies:
        template = lookup.template_of(library, _required_template(seat))
        seat_index = len(seats)
        seats.append(_template_seat(template))
        identities.append(Identity.ALLY)
        skills.append(
            _template_skills(
                template, library=library, seed=run.seed, node_key=node_key, seat=seat_index
            )
        )
        if seat.protected:
            protected_ally = seat_index
        if seat.boss:
            boss = seat_index

    enemies = (
        _drawn_enemies(battle, library=library, seed=run.seed, node_key=node_key)
        if isinstance(battle.enemies, EnemyDraw)
        else _fixed_enemies(battle, library)
    )
    enemy_templates: list[str] = []
    for seat, template in enemies:
        seat_index = len(seats)
        seats.append(_template_seat(template))
        identities.append(Identity.ENEMY)
        skills.append(
            _template_skills(
                template, library=library, seed=run.seed, node_key=node_key, seat=seat_index
            )
        )
        enemy_templates.append(template.id)
        if seat.boss:
            boss = seat_index

    for resolved in resolve_scaling(
        battle=battle, layer=layer, col=node.col, library=library
    ):
        for seat_index in _sides_for(resolved.side, identities):
            seats[seat_index] = _scaled(seats[seat_index], resolved.scale, stream)

    if battle.victory.kind is PveVictoryKind.SLAY_BOSS and boss is None:
        raise ScriptError(
            rule_id="script.compile.boss_seat_missing",
            loc=f"{room.id}/battle/victory",
            reason=(
                f"房间 {room.id!r} 的胜负是 slay_boss，但没有任何敌方座位标 boss: true："
                "编不出一个能判胜的 header"
            ),
        )

    return CompiledBattle(
        pve=PveConfig(
            identities=tuple(identities),
            principal=0,
            protected_ally=protected_ally,
            victory=battle.victory,
            boss=boss if battle.victory.kind is PveVictoryKind.SLAY_BOSS else None,
            grants=_grants(run=run, skills=skills),
            seats=tuple(seats),
        ),
        battle_seed=battle_seed,
        enemy_templates=tuple(enemy_templates),
    )


def _grants(*, run: RunState, skills: Sequence[Sequence[str]]) -> tuple[SkillGrant, ...]:
    """授予：主角的出战技能 + 遗物，随后逐座 AI 座位的模板技能（座位序）。"""
    grants: list[SkillGrant] = [
        SkillGrant(seat=0, kind="skill", id=skill_id) for skill_id in skills[0]
    ]
    grants.extend(SkillGrant(seat=0, kind="relic", id=relic) for relic in run.relics)
    for seat_index in range(1, len(skills)):
        grants.extend(
            SkillGrant(seat=seat_index, kind="skill", id=skill_id)
            for skill_id in skills[seat_index]
        )
    return tuple(grants)


# ---------------------------------------------------------------------------
# 进入节点 = 一次抽完
# ---------------------------------------------------------------------------


def enter_node(
    *,
    run: RunState,
    library: ScriptLibrary,
    node_id: str,
    value_table: SkillValueTable,
) -> RunState:
    """进入一个节点：先查可到达集，再把这一节点的随机**一次抽完**写进 ``pending``。

    返回的 ``RunState`` 是**新的一份**（``RunState`` 不可变）：``pending`` 非空的它既是
    恢复点，也是"续档不重抽"的同义反复——节点内不再有任何抽签。

    :raises ScriptError: 已经在一个节点里 / 节点不在可到达集里（已结算 / 被标签挡住 /
        不属于本大节）。
    :raises DeadEndError: 当前节点的出边全被挡住等死路（票 16 的那个出口）。
    """
    if run.pending is not None:
        raise ScriptError(
            rule_id="script.enter.node_in_progress",
            loc=run.pending.node_id,
            reason=(
                "当前节点尚未结算：一个节点是一个事务（票 04 §0 纪律 2），"
                "结算之后才谈得上进入下一个"
            ),
        )
    script = lookup.script_of(library, run.script_id)
    layer = lookup.layer_of(script, run.layer_index)
    candidates = available_next(layer, flags=run.flags, node_status=run.node_status)
    node = next((node for node in candidates if node.id == node_id), None)
    if node is None:
        raise ScriptError(
            rule_id="script.enter.unreachable_node",
            loc=progress_key(layer.id, node_id),
            reason=(
                f"节点 {node_id!r} 不在当前可到达集里（可进的是 "
                f"{[candidate.id for candidate in candidates]}）："
                "已结算 / 被标签挡住 / 不属于本大节的节点都进不去"
            ),
        )
    room = lookup.room_of(library, node.room)
    spec = materialise_spec(
        run=run, library=library, layer=layer, node=node, room=room, value_table=value_table
    )
    return run.model_copy(
        update={"pending": Pending(node_id=progress_key(layer.id, node.id), spec=spec)}
    )


def materialise_spec(
    *,
    run: RunState,
    library: ScriptLibrary,
    layer: Layer,
    node: Node,
    room: Room,
    value_table: SkillValueTable,
) -> PendingSpec:
    """把一个房间的全部随机抽完，物化成 ``pending.spec``（五选一）。"""
    if room.kind is RoomKind.UNKNOWN:
        inner = _revealed_room(run=run, library=library, layer=layer, node=node, room=room)
        if inner.kind is RoomKind.UNKNOWN:
            raise ScriptError(
                rule_id="script.enter.recursive_question_room",
                loc=inner.id,
                reason=(
                    f"问号房 {room.id!r} 的池里又抽到问号房 {inner.id!r}："
                    "问号池禁止递归（票 03 §2）"
                ),
            )
        return UnknownSpec(
            kind="unknown",
            revealed=RevealedSpec(
                room=inner.id,
                spec=materialise_spec(
                    run=run,
                    library=library,
                    layer=layer,
                    node=node,
                    room=inner,
                    value_table=value_table,
                ),
            ),
        )
    if room.kind in (RoomKind.STORY, RoomKind.BATTLE):
        return _battle_spec(
            run=run, library=library, layer=layer, node=node, room=room, value_table=value_table
        )
    if room.kind is RoomKind.EVENT:
        return _event_spec(run=run, library=library, layer=layer, node=node, room=room)
    return _shop_spec(run=run, library=library, layer=layer, node=node, value_table=value_table)


def _battle_spec(
    *,
    run: RunState,
    library: ScriptLibrary,
    layer: Layer,
    node: Node,
    room: Room,
    value_table: SkillValueTable,
) -> PendingSpec:
    """战斗节点：种子 + 编译好的 header + 各轮奖励候选（+ Boss 那件遗物）。"""
    compiled = compile_battle(room=room, node=node, layer=layer, library=library, run=run)
    node_key = progress_key(layer.id, node.id)
    sizes = rewards.battle_rounds(
        room=room, enemy_templates=compiled.enemy_templates, library=library
    )
    boss_relic = (
        rewards.draw_boss_relic(
            run=run,
            value_table=value_table,
            seed=run.seed,
            node_key=node_key,
            index=len(sizes),
        )
        if room.is_boss
        else None
    )
    return BattleSpec(
        kind="battle",
        battle_seed=compiled.battle_seed,
        pve=compiled.pve,
        rewards=rewards.draw_candidates(
            sizes=sizes,
            pool=rewards.clean_pool(run, value_table),
            value_table=value_table,
            seed=run.seed,
            node_key=node_key,
        ),
        enemy_templates=compiled.enemy_templates,
        boss_relic=boss_relic,
    )


def _event_spec(
    *,
    run: RunState,
    library: ScriptLibrary,
    layer: Layer,
    node: Node,
    room: Room,
) -> PendingSpec:
    """事件节点：从事件池抽一条事件（后果里的随机也在这一步物化）。"""
    pool_id = room.event_pool
    if pool_id is None:
        raise ScriptError(
            rule_id="script.enter.missing_event_pool",
            loc=room.id,
            reason=f"事件房 {room.id!r} 没有 event_pool：抽不出事件",
        )
    pool = lookup.event_pool_of(library, pool_id)
    if not pool.events:
        raise ScriptError(
            rule_id="script.enter.empty_event_pool",
            loc=pool.id,
            reason=f"事件池 {pool.id!r} 是空的：抽不出事件",
        )
    stream = event_stream(run.seed, progress_key(layer.id, node.id))
    event_id = pool.events[stream.randbelow(len(pool.events))]
    event = lookup.event_of(library, event_id)
    return EventSpec(
        kind="event", event_id=event_id, outcome_rolls=_outcome_rolls(event, stream)
    )


def _outcome_rolls(event: Event, stream: RandomSource) -> dict[int, object]:
    """事件后果里的随机（票 03 §5 的 ``script.event:<节点>``）：v1 **恒空**。

    数据模型的四种 ``EventEffect``（铜钱 / 标签 / 技能 / 遗物）**全是定值**，没有可抽的
    分量；这一格仍随节点进入而物化（形状照票 04 §1 不动），将来出现"随机金额 / 随机选项"
    时只改这里，不动 ``EventSpec``。
    """
    del event, stream
    return {}


def _shop_spec(
    *,
    run: RunState,
    library: ScriptLibrary,
    layer: Layer,
    node: Node,
    value_table: SkillValueTable,
) -> PendingSpec:
    """商店节点：6 技能 + 2 遗物，一次抽完、进档、售完不补货（票 03 §1）。"""
    skills, relics = rewards.shop_stock(
        run=run,
        value_table=value_table,
        seed=run.seed,
        node_key=progress_key(layer.id, node.id),
    )
    return ShopSpec(kind="shop", stock=ShopStock(skills=skills, relics=relics))


def _revealed_room(
    *,
    run: RunState,
    library: ScriptLibrary,
    layer: Layer,
    node: Node,
    room: Room,
) -> Room:
    """问号房揭晓：按加权池抽一个**非 unknown** 房间（票 03 §2）。"""
    pool = room.pool or ()
    if not pool:
        raise ScriptError(
            rule_id="script.enter.empty_question_pool",
            loc=room.id,
            reason=f"问号房 {room.id!r} 的加权池是空的：揭晓不出内层房间",
        )
    weights = {entry.room: entry.weight for entry in pool}
    picked = weighted_sample(
        pool=tuple(entry.room for entry in pool),
        weights=weights,
        count=1,
        source=unknown_stream(run.seed, progress_key(layer.id, node.id)),
    )
    if not picked:
        raise ScriptError(
            rule_id="script.enter.empty_question_pool",
            loc=room.id,
            reason=f"问号房 {room.id!r} 的权重全为 0：抽不出内层房间",
        )
    return lookup.room_of(library, picked[0])
