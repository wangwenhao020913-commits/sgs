"""剧本库：实体类型、三段校验、加载与内容指纹（M7 票 14）。

一个**库** = 剧本 / 大节 / 节点 / 边 / 房间 / 战斗 / 座位 / 敌人模板 / 技能池 / 事件 /
事件池 / 全局副本技能（票 03 §1）。它和内容包是两层数据（spec §7）：内容包管牌与武将面，
剧本库管路线与敌人编排；两者**同住** ``sgs/data/files/``（单层 glob 随包），但版本、校验器
与错误码各自独立。

四件套（票 03 §7）在本模块只落三件，第四件 ``script_mode_version`` 住存档（票 18）：

- ``script_schema_version``：**库文件头**，结构变了才动；认不出 ⇒ 版本门直接拒；
- ``script.version``：``Script`` 上的语义版本，编辑器每次保存 +1；**不用于拒绝**；
- ``script_digest``：规范化 dump 的 sha256（:func:`script_digest` / :func:`library_digest`），
  只做审计指纹与展示，**不用于拒绝**。

⚠ ``script_digest`` 不落成 ``Script`` 的字段：一个对象的指纹不能包含它自己（写入谁先谁后
都会变成自指），且票 24 的口径就是"保存时**现算**"。它因此是函数而不是数据。

**三段门**沿用 ``loader.py`` 的既有形状：版本 → 结构（pydantic，``extra="forbid"``）→
语义（手写、**全量聚合**）。区别只有一处，且是 D30 的裁定：

    ``validate_script_library`` 是**纯函数**，只**返回** ``LoadError`` 清单，不抛。
    "拒绝"是**调用方**的策略：运行期走严格（:func:`load_script_library`，有断链 ⇒ 拒，
    ``DataLoadError``），编辑器走宽松（``strict=False``，坏库照常加载，清单交给校验面板）。

把"拒绝"写进校验函数会让编辑器删完一个被引用的实体之后**再也打不开**那个库（D30 的两个
死结）。所以严格 / 宽松两条路都只是对同一份清单的处置，绝不是第二份校验。

**跨表引用一次全扫**（票 03 §6）：节点 → 房间、边 → 节点、战斗 → 模板 / 全局技能、模板 →
技能池、池 → 技能 id（∈ ``CATALOG``）、事件池 → 事件、事件选项效果 → 技能 / 遗物 id、
问号池 → 房间、初始角色 → 武将 id（**跨全部武将表，不看 ``enabled``**）。
断链一次报完，``loc`` 指向**引用方**、``expected`` 点名被引用对象。

**战斗与座位是房间的内嵌结构**（``Room.battle`` / ``Battle.allies`` / ``Battle.enemies``）
而不是库级共享表：票 03 §1 的"被谁引用"列里没有它们，票 25 的编辑器实体清单也没有——
它们是房间的**归属**物，删房间即删战斗（D30 的归属 / 共享引用两分）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from enum import Enum
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import DataLoadError, LoadError
from .loader import _parse_json, _structural_errors, load_hero_tables
from .rule_config import PveVictory, PveVictoryKind

__all__ = [
    "KNOWN_SCRIPT_SCHEMA_VERSIONS",
    "SCRIPT_LIBRARY_FILENAME",
    "SCRIPT_SCHEMA_VERSION",
    "Battle",
    "CoinsEffect",
    "DrawSpec",
    "Edge",
    "EnemyDraw",
    "Event",
    "EventEffect",
    "EventOption",
    "EventPool",
    "GlobalSkill",
    "GlobalSkillRef",
    "GlobalSkillSide",
    "GrantRelicEffect",
    "GrantSkillEffect",
    "Grid",
    "Layer",
    "Node",
    "QuestionEntry",
    "RandomSkills",
    "Requirement",
    "Reward",
    "Room",
    "RoomClear",
    "RoomKind",
    "Scale",
    "Scaling",
    "Script",
    "ScriptLibrary",
    "Seat",
    "SeatRole",
    "SetFlagsEffect",
    "SkillPool",
    "Template",
    "canonical_json",
    "library_digest",
    "load_builtin_script_library",
    "load_script_library",
    "script_digest",
    "validate_script_library",
]

SCRIPT_SCHEMA_VERSION: Final = 1
"""剧本库认得的 ``script_schema_version``（**库文件头**；认不出就报错重来，不做迁移）。"""

KNOWN_SCRIPT_SCHEMA_VERSIONS: frozenset[int] = frozenset({SCRIPT_SCHEMA_VERSION})
"""版本门的合法值集合；与内容包 / 武将表的版本集合**分开**（三份数据各自升版）。"""

SCRIPT_LIBRARY_FILENAME: Final = "scripts.huangjin.json"
"""内置剧本库的文件名（住 ``sgs/data/files/`` 单层目录：随包、不用改 ``pyproject.toml``）。"""

Id = Annotated[str, Field(min_length=1)]
"""引用用的稳定 id：非空字符串（空 id 会让"被谁引用"失去意义）。"""

Span = tuple[int, int]
"""闭区间 ``[min, max]``（JSON 里是两元素数组；合法性在语义门里查）。"""


class _Frozen(BaseModel):
    """本模块所有实体的共同纪律：不可变、无多余字段（拼错的键当场判红）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# 闭集（票 03 §1–§3）
# ---------------------------------------------------------------------------


class RoomKind(str, Enum):
    """房间五类（**没有休整**，票 03 §1；"休整"在票 02 收口后被取消）。"""

    STORY = "story"
    """剧情战斗：败 ⇒ 整轮结束。"""

    BATTLE = "battle"
    """意外战斗：败 ⇒ 只关掉这个节点。"""

    EVENT = "event"
    SHOP = "shop"
    UNKNOWN = "unknown"
    """问号房：进入时从自己的加权池里抽一个房间并即刻揭晓。"""


class SeatRole(str, Enum):
    """座位自述（票 03 §1）。

    两侧列表只收对应的一侧：``allies`` 里只能是 ``ALLY``、``enemies`` 里只能是 ``ENEMY``。
    ``PLAYER`` 是**隐式 0 号座**（票 03 §3「主角恒为 0 号」），数据里没有它的座位条目——
    写上它是数据错误（语义门报 ``script_library.seat_role_mismatch``）。
    """

    PLAYER = "player"
    ALLY = "ally"
    ENEMY = "enemy"


class GlobalSkillSide(str, Enum):
    """副本全局技能对哪一侧生效（票 03 §1）。"""

    ALLY = "ally"
    ENEMY = "enemy"
    ALL = "all"


_PAYLOAD_BY_KIND: Mapping[RoomKind, tuple[str, ...]] = MappingProxyType(
    {
        RoomKind.STORY: ("battle",),
        RoomKind.BATTLE: ("battle",),
        RoomKind.EVENT: ("event_pool",),
        RoomKind.SHOP: (),
        RoomKind.UNKNOWN: ("pool",),
    }
)
"""``kind`` → 必填载荷（票 03 §1）：剧情 / 意外战斗带 ``battle``、事件带 ``event_pool``、
问号房带加权 ``pool``、商店三样都不带（库存由价值表现抽）。"""


# ---------------------------------------------------------------------------
# 小结构
# ---------------------------------------------------------------------------


class Requirement(_Frozen):
    """标签条件（票 03 §2）：边 / 事件选项共用同一套。

    ``flags`` = 必须具备，``not_flags`` = 必须没有。空条件 = 无条件（缺省）。
    """

    flags: tuple[Id, ...] = ()
    not_flags: tuple[Id, ...] = ()


class Reward(_Frozen):
    """一份定值奖励（票 03 §4）：铜钱 + 三档 ``pick k`` 的次数。

    "pick k" = 从学习池按稀有度权重抽 k 张、玩家选 1 张，共弹 k 次。结算时**全队模板
    奖励累加**，再 + 房间 ``extra_reward``。
    """

    coins: int = Field(default=0, ge=0)
    pick1: int = Field(default=0, ge=0)
    pick2: int = Field(default=0, ge=0)
    pick3: int = Field(default=0, ge=0)


class Scale(_Frozen):
    """三档属性缩放的区间（票 03 §3）：``hp`` / ``draw`` / ``slash`` 各 ``[min, max]``。

    三个字段都可缺省：房间级引用按字段**覆盖**大节 ``default_scaling`` 的对应档。
    """

    hp: Span | None = None
    draw: Span | None = None
    slash: Span | None = None


class Scaling(_Frozen):
    """大节级默认曲线的一段（票 03 §3）：``from_col`` 起（含）套用这三个区间。"""

    from_col: int = Field(ge=0)
    hp: Span
    draw: Span
    slash: Span


class Grid(_Frozen):
    """大节的网格（票 03 §1–§2）：一格一节点，列 = 进度轴。"""

    rows: int = Field(ge=1)
    cols: int = Field(ge=1)


class Node(_Frozen):
    """棋盘上的一个节点：占一格、指向一个房间（票 03 §1）。"""

    id: Id
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    room: Id


class Edge(_Frozen):
    """作者画的一条边（票 03 §2）：只许 ``to.col > from.col``。

    ``from`` 是 Python 关键字，故字段名取 ``from_``、JSON 键仍是 ``from``
    （``populate_by_name`` + 序列化别名 ⇒ 磁盘上的形状与票面一字不差）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    from_: Id = Field(alias="from")
    to: Id
    requires: Requirement = Field(default_factory=Requirement)


class QuestionEntry(_Frozen):
    """问号房加权池的一项（票 03 §2）：``weight`` 是正整数（结构门查）。"""

    room: Id
    weight: int = Field(ge=1)


class RoomClear(_Frozen):
    """房间完成时的后果（票 03 §2）：v1 只设标签。"""

    set_flags: tuple[Id, ...] = ()


class SkillPool(_Frozen):
    """技能池（票 03 §1）：``skills`` 是 ``CATALOG`` 里的技能 id，可混正规与副本 NPC 技能。"""

    id: Id
    name: str
    skills: tuple[Id, ...] = ()


class RandomSkills(_Frozen):
    """模板的随机技能抽取（票 03 §1）：从 ``pool`` 无放回抽 ``count`` 条。

    ``count = [0, 0]`` = **固定对手**（"固定对手就是随机池为空的模板"，票 03 §1）。
    """

    pool: Id
    count: Span = (0, 0)


class Template(_Frozen):
    """敌人模板（票 03 §1）：敌人不是武将表记录，而是「模板 + 抽到的技能 + 属性加成」。"""

    id: Id
    name: str
    band: Id
    """档位：**字符串标签**（票 17 备注）；按档抽只在同 band 内抽。"""

    hp: int = Field(ge=1)
    hp_max: int = Field(ge=1)
    initial_skills: tuple[Id, ...] = ()
    random: RandomSkills
    reward: Reward = Field(default_factory=Reward)


class Seat(_Frozen):
    """战斗里的一个座位（票 03 §1、§3）：AI 队友或敌人。

    主角**不在**这里：它是隐式 0 号座（票 03 §3），故两个列表里写 ``role: player``
    会被语义门拒。
    """

    role: SeatRole
    template: Id | None = None
    protected: bool = False
    boss: bool = False


class DrawSpec(_Frozen):
    """按档抽敌人（票 03 §3）：从同 ``band`` 的模板里无放回抽 ``count`` 个。"""

    band: Id
    count: Span


class EnemyDraw(_Frozen):
    """``enemies`` 的"按档抽"形态（票 03 §3）；固定形态是座位数组。"""

    draw: DrawSpec


class GlobalSkill(_Frozen):
    """副本全局技能的**数据定义**（票 03 §1、§3）：对 ``side`` 指定的每座缩放三档属性。

    它的 id 是**库内** id，不是 ``CATALOG`` 里的技能：v1 的全局技能只做属性缩放，
    效果由这里的三档区间完全确定（票 03 §10.3）。
    """

    id: Id
    name: str
    side: GlobalSkillSide
    scale: Scale


class GlobalSkillRef(_Frozen):
    """战斗对全局技能的引用（票 03 §3）：可带 ``scale`` 覆盖区间。"""

    skill: Id
    scale: Scale | None = None


class Battle(_Frozen):
    """一场战斗的编排（票 03 §3–§4）。

    ``enemies`` 两路都支持：固定的座位数组，或 ``{draw: {band, count}}`` 按档抽。
    """

    allies: tuple[Seat, ...] = ()
    enemies: tuple[Seat, ...] | EnemyDraw
    victory: PveVictory
    global_skills: tuple[GlobalSkillRef, ...] = ()
    extra_reward: Reward = Field(default_factory=Reward)


class CoinsEffect(_Frozen):
    """事件效果：铜钱增减（正负都行；负 = 花费）。"""

    kind: Literal["coins"]
    amount: int


class SetFlagsEffect(_Frozen):
    """事件效果：设 run 级标签（票 03 §2 的标签闭环）。"""

    kind: Literal["set_flags"]
    flags: tuple[Id, ...]


class GrantSkillEffect(_Frozen):
    """事件效果：直接给一条技能（进背包）。"""

    kind: Literal["grant_skill"]
    skill: Id


class GrantRelicEffect(_Frozen):
    """事件效果：直接给一件遗物。"""

    kind: Literal["grant_relic"]
    relic: Id


EventEffect = Annotated[
    CoinsEffect | SetFlagsEffect | GrantSkillEffect | GrantRelicEffect,
    Field(discriminator="kind"),
]
"""事件选项的效果（票 03 §1 的 ``options[].effects``）：按 ``kind`` 判别。"""


class EventOption(_Frozen):
    """事件的一个选项（票 03 §1）：``requires`` 与边共用标签条件。"""

    text: str
    requires: Requirement = Field(default_factory=Requirement)
    effects: tuple[EventEffect, ...] = ()


class Event(_Frozen):
    """事件（票 03 §1）：一段文本 + 若干选项。"""

    id: Id
    name: str
    text: str = ""
    options: tuple[EventOption, ...] = ()


class EventPool(_Frozen):
    """事件池（票 03 §1）：房间按 id 引用。"""

    id: Id
    name: str
    events: tuple[Id, ...] = ()


class Room(_Frozen):
    """房间（票 03 §1–§2）：``kind`` 决定它带哪一份载荷。

    - ``story`` / ``battle`` ⇒ 必须有 ``battle``；
    - ``event`` ⇒ 必须有 ``event_pool``；
    - ``unknown`` ⇒ 必须有非空 ``pool``（问号房的加权池）；
    - ``shop`` ⇒ 三样都不带（库存由价值表现抽）。

    ``is_boss`` 只是**房间语义**（失败即整轮结束、Boss 结算顺序），形状与普通房间同一份。
    """

    id: Id
    name: str
    kind: RoomKind
    text: str = ""
    battle: Battle | None = None
    event_pool: Id | None = None
    pool: tuple[QuestionEntry, ...] | None = None
    is_boss: bool = False
    on_clear: RoomClear = Field(default_factory=RoomClear)


class Layer(_Frozen):
    """大节（票 03 §1–§3）：网格 + 节点 + 边 + 默认缩放曲线。"""

    id: Id
    name: str
    grid: Grid
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    default_scaling: tuple[Scaling, ...] = ()


class Script(_Frozen):
    """剧本（票 03 §1）：``roster`` + 大节；大节是它**拥有**的（D30：删剧本级联删大节）。"""

    id: Id
    name: str
    version: int = Field(ge=1)
    """语义版本：编辑器每次保存 +1；**不用于拒绝**（票 03 §7）。"""

    roster: tuple[Id, ...] = ()
    """初始角色候选（3 选 1）：武将 id，**跨全部武将表、不看 ``enabled``**。"""

    layers: tuple[Layer, ...] = ()
    comment: str = ""
    """自由文本备注（作者注 / "数值占位"标记 / 列表页简介）；不影响语义与指纹之外的东西。"""


class ScriptLibrary(_Frozen):
    """一个剧本库（票 14 的进料单位）：库头 + 全部共享表 + 剧本。

    内置库与用户库**同 schema**（spec §7）：内置只是一个只读的库实例。
    """

    script_schema_version: int
    scripts: tuple[Script, ...] = ()
    rooms: tuple[Room, ...] = ()
    templates: tuple[Template, ...] = ()
    skill_pools: tuple[SkillPool, ...] = ()
    events: tuple[Event, ...] = ()
    event_pools: tuple[EventPool, ...] = ()
    global_skills: tuple[GlobalSkill, ...] = ()


# ---------------------------------------------------------------------------
# 内容指纹（票 03 §7：审计指纹 / 展示，**不用于拒绝**）
# ---------------------------------------------------------------------------


def canonical_json(value: BaseModel | Mapping[str, Any]) -> str:
    """**规范化 dump**：键序排序、无多余空白、UTF-8 原文。

    指纹的唯一来源是这一处：``script_digest`` / ``library_digest`` 与将来任何"内容指纹"
    都调它，不各自 ``json.dumps``（否则"同内容不同指纹"迟早出现）。
    """
    payload = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel)
        else value
    )
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: BaseModel) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def script_digest(script: Script) -> str:
    """一份剧本的内容指纹（``sha256:<hex>``）。

    只覆盖剧本自己的字段（roster / 大节 / 节点 / 边）。**共享表不在内**：房间 / 模板改了
    而剧本没动时，这一份剧本的指纹不变——这是有意的，``Script`` 的指纹回答"这一份剧本
    文档动过没有"，整库的内容指纹用 :func:`library_digest`。
    """
    return _digest(script)


def library_digest(library: ScriptLibrary) -> str:
    """整个库的内容指纹（含全部共享表；"另存为副本"前后比较用它）。"""
    return _digest(library)


# ---------------------------------------------------------------------------
# 三段门
# ---------------------------------------------------------------------------


def _script_version_error(file: str, parsed: Mapping[str, Any]) -> LoadError | None:
    """版本闸门：未知 / 缺失 ``script_schema_version`` 一律拒绝（fail-closed）。

    与 ``loader._version_error`` 同一判据、同一错误码家族，但键名与文案独立：剧本库的
    库头叫 ``script_schema_version``（票 03 §7），不是内容包的 ``schema_version``。
    """
    expected = f"剧本库结构版本之一：{sorted(KNOWN_SCRIPT_SCHEMA_VERSIONS)}"
    if "script_schema_version" not in parsed:
        return LoadError(
            file=file,
            loc="/script_schema_version",
            expected=expected,
            got="缺失",
            rule_id="version.missing",
        )
    value = parsed["script_schema_version"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in KNOWN_SCRIPT_SCHEMA_VERSIONS:
        return LoadError(
            file=file,
            loc="/script_schema_version",
            expected=expected,
            got=repr(value),
            rule_id="version.unknown",
        )
    return None


def _library_from_mapping(file: str, parsed: Mapping[str, Any]) -> ScriptLibrary:
    """版本 → 结构两道门（两档调用方都绕不开：模型建不出来就谈不上"宽松加载"）。"""
    version_error = _script_version_error(file, parsed)
    if version_error is not None:
        raise DataLoadError([version_error])
    try:
        return ScriptLibrary.model_validate(parsed)
    except ValidationError as exc:
        raise DataLoadError(_structural_errors(file, exc)) from exc


class _Entities(NamedTuple):
    """引用目标的**一次**索引：全部 id → 实体（武将只看 id 并集，不看 ``enabled``）。"""

    rooms: dict[str, Room]
    templates: dict[str, Template]
    skill_pools: dict[str, SkillPool]
    events: dict[str, Event]
    event_pools: dict[str, EventPool]
    global_skills: dict[str, GlobalSkill]
    heroes: frozenset[str]
    catalog: Mapping[str, Any]
    band_counts: dict[str, int]
    """``band`` → 同档模板数（按档抽的"无放回"够不够抽由它回答）。"""


@lru_cache(maxsize=None)
def _all_hero_ids() -> frozenset[str]:
    """跨**全部**武将表的 id 并集（票 03 §6：**不看** ``enabled``，同手选禁将的口径）。"""
    return frozenset(hero.id for table in load_hero_tables() for hero in table.heroes)


def _band_counts(templates: tuple[Template, ...]) -> dict[str, int]:
    """``band`` → 同档模板数：按档抽是**无放回**的，够不够抽要能机械回答。"""
    counts: dict[str, int] = {}
    for template in templates:
        counts[template.band] = counts.get(template.band, 0) + 1
    return counts


def _unique_index(
    errors: list[LoadError], file: str, table: str, entities: Any
) -> dict[str, Any]:
    """把一张表建成 ``id → 实体`` 的索引；重复 id 记一条（保留第一条，引用照常解析）。"""
    index: dict[str, Any] = {}
    for position, entity in enumerate(entities):
        if entity.id in index:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"/{table}/{position}/id",
                    expected=f"{table} 里唯一的 id",
                    got=entity.id,
                    rule_id="script_library.duplicate_id",
                )
            )
            continue
        index[entity.id] = entity
    return index


def _script_errors(
    file: str, position: int, script: Script, entities: _Entities
) -> list[LoadError]:
    """剧本：roster（3 选 1、跨表解析、不看 enabled）+ 大节。"""
    base = f"/scripts/{position}"
    errors: list[LoadError] = []
    if len(script.roster) != 3:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/roster",
                expected="恰好 3 名初始角色候选（开局 3 选 1）",
                got=f"{len(script.roster)} 名",
                rule_id="script_library.roster_size",
            )
        )
    seen_heroes: dict[str, int] = {}
    for index, hero_id in enumerate(script.roster):
        if hero_id in seen_heroes:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/roster/{index}",
                    expected="同一份 roster 里不重复的武将 id",
                    got=hero_id,
                    rule_id="script_library.duplicate_reference",
                )
            )
        elif hero_id not in entities.heroes:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/roster/{index}",
                    expected="武将表里存在的武将 id（跨全部武将表，**不看** enabled）",
                    got=hero_id,
                    rule_id="script_library.unknown_hero",
                )
            )
        else:
            seen_heroes[hero_id] = index
    seen_layers: dict[str, int] = {}
    for index, layer in enumerate(script.layers):
        if layer.id in seen_layers:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/layers/{index}/id",
                    expected="同一剧本里唯一的大节 id",
                    got=layer.id,
                    rule_id="script_library.duplicate_id",
                )
            )
        else:
            seen_layers[layer.id] = index
        errors.extend(
            _layer_errors(file, f"{base}/layers/{index}", layer, entities)
        )
    return errors


def _layer_errors(
    file: str, base: str, layer: Layer, entities: _Entities
) -> list[LoadError]:
    """大节：节点（网格内、一格一节点、指向房间）+ 边（列前进、端点存在、不重复）+ 曲线。"""
    errors: list[LoadError] = []
    node_positions: dict[str, int] = {}
    cells: dict[tuple[int, int], str] = {}
    for index, node in enumerate(layer.nodes):
        if node.id in node_positions:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/nodes/{index}/id",
                    expected="同一大节里唯一的节点 id",
                    got=node.id,
                    rule_id="script_library.duplicate_id",
                )
            )
        else:
            node_positions[node.id] = index
        if node.row >= layer.grid.rows:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/nodes/{index}/row",
                    expected=f"0 ~ {layer.grid.rows - 1}（grid.rows = {layer.grid.rows}）",
                    got=str(node.row),
                    rule_id="script_library.node_out_of_grid",
                )
            )
        if node.col >= layer.grid.cols:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/nodes/{index}/col",
                    expected=f"0 ~ {layer.grid.cols - 1}（grid.cols = {layer.grid.cols}）",
                    got=str(node.col),
                    rule_id="script_library.node_out_of_grid",
                )
            )
        cell = (node.row, node.col)
        occupied = cells.get(cell)
        if occupied is not None:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/nodes/{index}",
                    expected="一格一节点（这一格只属于一个节点）",
                    got=f"({node.row}, {node.col}) 已被 {occupied} 占用",
                    rule_id="script_library.cell_taken",
                )
            )
        else:
            cells[cell] = node.id
        if node.room not in entities.rooms:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/nodes/{index}/room",
                    expected="rooms 里存在的房间 id",
                    got=node.room,
                    rule_id="script_library.unknown_room",
                )
            )
    seen_edges: dict[tuple[str, str], int] = {}
    for index, edge in enumerate(layer.edges):
        for field, node_id in (("from", edge.from_), ("to", edge.to)):
            if node_id not in node_positions:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/edges/{index}/{field}",
                        expected="同一大节里存在的节点 id",
                        got=node_id,
                        rule_id="script_library.unknown_node",
                    )
                )
        pair = (edge.from_, edge.to)
        if pair in seen_edges:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/edges/{index}",
                    expected="不重复的 (from, to) 连线（同一对节点只画一次）",
                    got=f"{edge.from_} → {edge.to}",
                    rule_id="script_library.duplicate_reference",
                )
            )
        else:
            seen_edges[pair] = index
        start = (
            layer.nodes[node_positions[edge.from_]]
            if edge.from_ in node_positions
            else None
        )
        end = (
            layer.nodes[node_positions[edge.to]] if edge.to in node_positions else None
        )
        if start is not None and end is not None and end.col <= start.col:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/edges/{index}/to",
                    expected="to.col > from.col（列 = 进度轴，不能回头 / 同列互连）",
                    got=f"from.col={start.col} / to.col={end.col}",
                    rule_id="script_library.edge_not_forward",
                )
            )
    previous_from_col = -1
    for index, scaling in enumerate(layer.default_scaling):
        field = f"{base}/default_scaling/{index}"
        if scaling.from_col <= previous_from_col:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{field}/from_col",
                    expected="严格升序的 from_col（继承按「最后一段 from_col ≤ 当前列」取）",
                    got=f"from_col={scaling.from_col}（前一段 {previous_from_col}）",
                    rule_id="script_library.scaling_not_ascending",
                )
            )
        previous_from_col = max(previous_from_col, scaling.from_col)
        if scaling.from_col >= layer.grid.cols:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{field}/from_col",
                    expected=f"0 ~ {layer.grid.cols - 1}（grid.cols = {layer.grid.cols}）",
                    got=str(scaling.from_col),
                    rule_id="script_library.scaling_out_of_grid",
                )
            )
        for name, span in _scale_fields(scaling):
            if span is not None and span[0] > span[1]:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{field}/{name}",
                        expected="[min, max] 且 min <= max",
                        got=f"[{span[0]}, {span[1]}]",
                        rule_id="script_library.bad_span",
                    )
                )
    return errors


def _seat_errors(
    file: str,
    base: str,
    seat: Seat,
    role: SeatRole,
    list_label: str,
    entities: _Entities,
) -> list[LoadError]:
    """座位：``role`` 与所在列表一致、必须用模板、模板必须存在。"""
    errors: list[LoadError] = []
    if seat.role is not role:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/role",
                expected=(
                    f"role = {role.value}（{list_label} 里的座位都是这一侧；"
                    "主角是隐式 0 号座）"
                ),
                got=seat.role.value,
                rule_id="script_library.seat_role_mismatch",
            )
        )
    if seat.template is None:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/template",
                expected="模板 id（AI 座位用模板：敌人不是武将表记录）",
                got="缺失",
                rule_id="script_library.seat_without_template",
            )
        )
    elif seat.template not in entities.templates:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/template",
                expected="templates 里存在的敌人模板 id",
                got=seat.template,
                rule_id="script_library.unknown_template",
            )
        )
    return errors


def _scale_fields(model: Scale | Scaling) -> Iterator[tuple[str, Span | None]]:
    """三档区间的 ``(字段名, 区间)``：清单的唯一真源是 :class:`Scale` 的字段表。

    不在两个校验器里重抄 ``hp / draw / slash``——重抄的清单迟早与模型漂开一格。
    """
    for name in Scale.model_fields:
        yield name, getattr(model, name)


def _scale_errors(
    file: str, base: str, scale: Scale, *, require_all: bool
) -> list[LoadError]:
    """三档属性区间：定义处必须齐全，引用处可以逐字段覆盖；两处都查 ``min <= max``。"""
    errors: list[LoadError] = []
    spans = tuple(_scale_fields(scale))
    missing = [name for name, span in spans if span is None]
    if require_all and missing:
        errors.append(
            LoadError(
                file=file,
                loc=base,
                expected="hp / draw / slash 三档区间齐全（副本全局技能的定义）",
                got="缺 " + "、".join(missing),
                rule_id="script_library.scale_incomplete",
            )
        )
    for name, span in spans:
        if span is not None and span[0] > span[1]:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/{name}",
                    expected="[min, max] 且 min <= max",
                    got=f"[{span[0]}, {span[1]}]",
                    rule_id="script_library.bad_span",
                )
            )
    return errors


def _battle_errors(
    file: str, base: str, battle: Battle, entities: _Entities
) -> list[LoadError]:
    """战斗：座位数与两侧数量、座位自述、胜负条件与 Boss 座位、全局技能引用。"""
    errors: list[LoadError] = []
    if len(battle.allies) > 2:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/allies",
                expected="0 ~ 2 座 AI 队友",
                got=f"{len(battle.allies)} 座",
                rule_id="script_library.ally_count",
            )
        )
    for index, seat in enumerate(battle.allies):
        errors.extend(
            _seat_errors(
                file,
                f"{base}/allies/{index}",
                seat,
                SeatRole.ALLY,
                "allies（AI 队友）",
                entities,
            )
        )
    enemies = battle.enemies
    if isinstance(enemies, EnemyDraw):
        low, high = enemies.draw.count
        enemy_span_legal = 1 <= low <= high <= 4
        if not enemy_span_legal:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/enemies/draw/count",
                    expected="1 <= min <= max <= 4（按档抽的敌人数）",
                    got=f"[{low}, {high}]",
                    rule_id="script_library.enemy_count",
                )
            )
        enemy_seats: tuple[Seat, ...] = ()
        enemy_span = (low, high)
        available = entities.band_counts.get(enemies.draw.band, 0)
        if available == 0:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/enemies/draw/band",
                    expected="至少一条敌人模板的 band",
                    got=enemies.draw.band,
                    rule_id="script_library.unknown_band",
                )
            )
        elif enemy_span_legal and high > available:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/enemies/draw/count",
                    expected=f"不超过同 band 的模板数（{available} 条）",
                    got=f"[{low}, {high}]",
                    rule_id="script_library.draw_exceeds_band",
                )
            )
    else:
        enemy_span_legal = 1 <= len(enemies) <= 4
        if not enemy_span_legal:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/enemies",
                    expected="1 ~ 4 座敌人",
                    got=f"{len(enemies)} 座",
                    rule_id="script_library.enemy_count",
                )
            )
        enemy_seats = enemies
        enemy_span = (len(enemies), len(enemies))
    for index, seat in enumerate(enemy_seats):
        errors.extend(
            _seat_errors(
                file,
                f"{base}/enemies/{index}",
                seat,
                SeatRole.ENEMY,
                "enemies（敌方）",
                entities,
            )
        )
    if len(battle.allies) <= 2 and enemy_span_legal:
        low_total = 1 + len(battle.allies) + enemy_span[0]
        high_total = 1 + len(battle.allies) + enemy_span[1]
        if low_total < 2 or high_total > 5:
            errors.append(
                LoadError(
                    file=file,
                    loc=base,
                    expected="座位总数 2 ~ 5（主角 1 + allies + enemies）",
                    got=f"{low_total}" if low_total == high_total else f"{low_total} ~ {high_total}",
                    rule_id="script_library.seat_count",
                )
            )
    boss_seats = [seat for seat in (*battle.allies, *enemy_seats) if seat.boss]
    for index, seat in enumerate(enemy_seats):
        if seat.protected:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/enemies/{index}/protected",
                    expected="受保护对象只能是一名 AI 队友（PveConfig.protected_ally 是 ALLY 座位）",
                    got="protected = true",
                    rule_id="script_library.protected_seat",
                )
            )
    protected_allies = [
        index for index, seat in enumerate(battle.allies) if seat.protected
    ]
    if len(protected_allies) > 1:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/allies",
                expected="最多一名受保护队友（逐场只有一个 protected_ally）",
                got=f"{len(protected_allies)} 名",
                rule_id="script_library.protected_seat",
            )
        )
    victory = battle.victory
    if victory.kind is PveVictoryKind.SLAY_BOSS:
        if len(boss_seats) != 1:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/victory",
                    expected="victory 是 slay_boss ⇒ 恰好一个 boss: true 的敌方座位",
                    got=f"{len(boss_seats)} 个 boss 座位",
                    rule_id="script_library.boss_seat",
                )
            )
    elif len(boss_seats) > 1:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/victory",
                expected="一场战斗最多一个 boss 座位",
                got=f"{len(boss_seats)} 个 boss 座位",
                rule_id="script_library.boss_seat",
            )
        )
    round_based = victory.kind in (
        PveVictoryKind.SURVIVE_ROUNDS,
        PveVictoryKind.ROUT_WITHIN_ROUNDS,
    )
    if round_based and (victory.rounds is None or victory.rounds < 1):
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/victory",
                expected=f"{victory.kind.value} 必须带正整数 rounds",
                got=repr(victory.rounds),
                rule_id="script_library.victory_rounds",
            )
        )
    elif not round_based and victory.rounds is not None:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/victory",
                expected=f"{victory.kind.value} 不接受 rounds",
                got=repr(victory.rounds),
                rule_id="script_library.victory_rounds",
            )
        )
    for index, reference in enumerate(battle.global_skills):
        if reference.skill not in entities.global_skills:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/global_skills/{index}/skill",
                    expected="global_skills 里存在的副本全局技能 id",
                    got=reference.skill,
                    rule_id="script_library.unknown_global_skill",
                )
            )
        if reference.scale is not None:
            errors.extend(
                _scale_errors(
                    file,
                    f"{base}/global_skills/{index}/scale",
                    reference.scale,
                    require_all=False,
                )
            )
    return errors


def _question_pool_errors(
    file: str, base: str, pool: tuple[QuestionEntry, ...], entities: _Entities
) -> list[LoadError]:
    """问号池（票 03 §2）：非空、只引用非 unknown 房间、同池不重复、权重正整数。"""
    errors: list[LoadError] = []
    if not pool:
        errors.append(
            LoadError(
                file=file,
                loc=base,
                expected="非空的问号房加权池",
                got="0 项",
                rule_id="script_library.pool_empty",
            )
        )
    seen: dict[str, int] = {}
    for index, entry in enumerate(pool):
        target = entities.rooms.get(entry.room)
        if target is None:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/{index}/room",
                    expected="rooms 里存在的房间 id",
                    got=entry.room,
                    rule_id="script_library.unknown_room",
                )
            )
        elif target.kind is RoomKind.UNKNOWN:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/{index}/room",
                    expected="非 unknown 房间（问号池不许递归）",
                    got=entry.room,
                    rule_id="script_library.unknown_room_recursive",
                )
            )
        if entry.room in seen:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/{index}",
                    expected="同一问号池里不重复的房间 id",
                    got=entry.room,
                    rule_id="script_library.duplicate_reference",
                )
            )
        else:
            seen[entry.room] = index
    return errors


def _room_errors(
    file: str, position: int, room: Room, entities: _Entities
) -> list[LoadError]:
    """房间：``kind`` 决定载荷；``is_boss`` 只属于战斗房；载荷里的引用一次查完。"""
    base = f"/rooms/{position}"
    errors: list[LoadError] = []
    payloads = (
        ("battle", room.battle),
        ("event_pool", room.event_pool),
        ("pool", room.pool),
    )
    required = _PAYLOAD_BY_KIND[room.kind]
    for name, value in payloads:
        if name in required:
            if value is None:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/{name}",
                        expected=f"{room.kind.value} 房间必须带 {name} 载荷",
                        got="缺失",
                        rule_id="script_library.room_payload_missing",
                    )
                )
        elif value is not None:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/{name}",
                    expected=f"{room.kind.value} 房间不带 {name}（载荷由 kind 决定）",
                    got="写了",
                    rule_id="script_library.room_payload_unexpected",
                )
            )
    if room.is_boss and room.kind not in (RoomKind.STORY, RoomKind.BATTLE):
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/is_boss",
                expected="只有 story / battle 房间可以标 is_boss",
                got=f"kind={room.kind.value}",
                rule_id="script_library.boss_room_not_a_battle",
            )
        )
    if room.battle is not None:
        errors.extend(_battle_errors(file, f"{base}/battle", room.battle, entities))
    if room.pool is not None:
        errors.extend(_question_pool_errors(file, f"{base}/pool", room.pool, entities))
    if room.event_pool is not None and room.event_pool not in entities.event_pools:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/event_pool",
                expected="event_pools 里存在的事件池 id",
                got=room.event_pool,
                rule_id="script_library.unknown_event_pool",
            )
        )
    return errors


def _template_errors(
    file: str, position: int, template: Template, entities: _Entities
) -> list[LoadError]:
    """模板：体力、随机抽取的池与次数、初始技能（∈ ``CATALOG``）。"""
    base = f"/templates/{position}"
    errors: list[LoadError] = []
    if template.hp > template.hp_max:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/hp",
                expected=f"不超过体力上限（hp_max = {template.hp_max}）",
                got=str(template.hp),
                rule_id="script_library.template_hp",
            )
        )
    pool = entities.skill_pools.get(template.random.pool)
    if pool is None:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/random/pool",
                expected="skill_pools 里存在的技能池 id",
                got=template.random.pool,
                rule_id="script_library.unknown_pool",
            )
        )
    low, high = template.random.count
    if not 0 <= low <= high:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/random/count",
                expected="0 <= min <= max 的两元素区间（[0, 0] = 固定对手）",
                got=f"[{low}, {high}]",
                rule_id="script_library.random_count",
            )
        )
    elif pool is not None and high > len(pool.skills):
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/random/count",
                expected=f"不超过技能池里的技能数（{len(pool.skills)} 条）",
                got=f"[{low}, {high}]",
                rule_id="script_library.random_count_exceeds_pool",
            )
        )
    for index, skill_id in enumerate(template.initial_skills):
        if skill_id not in entities.catalog:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/initial_skills/{index}",
                    expected="CATALOG 里已登记的技能 id",
                    got=skill_id,
                    rule_id="script_library.unknown_skill",
                )
            )
    return errors


def _skill_pool_errors(
    file: str, position: int, pool: SkillPool, entities: _Entities
) -> list[LoadError]:
    """技能池：``skills`` 都要在 ``CATALOG`` 里，且同池不重复（无放回抽的前提）。

    每个引用点**恰好一条** ``LoadError``，未知 id 优先（票 35 与 :func:`_event_pool_errors`
    统一成这一口径：两张池表对同一份坏数据条数相同）。
    """
    base = f"/skill_pools/{position}"
    errors: list[LoadError] = []
    seen: dict[str, int] = {}
    for index, skill_id in enumerate(pool.skills):
        if skill_id not in entities.catalog:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/skills/{index}",
                    expected="CATALOG 里已登记的技能 id",
                    got=skill_id,
                    rule_id="script_library.unknown_skill",
                )
            )
        elif skill_id in seen:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/skills/{index}",
                    expected="同一技能池里不重复的技能 id",
                    got=skill_id,
                    rule_id="script_library.duplicate_reference",
                )
            )
        else:
            seen[skill_id] = index
    return errors


def _event_errors(
    file: str, position: int, event: Event, entities: _Entities
) -> list[LoadError]:
    """事件：效果里授予的技能 / 遗物必须是 ``CATALOG`` 里已登记的 id。"""
    base = f"/events/{position}"
    errors: list[LoadError] = []
    for option_index, option in enumerate(event.options):
        for effect_index, effect in enumerate(option.effects):
            loc = f"{base}/options/{option_index}/effects/{effect_index}"
            if isinstance(effect, GrantSkillEffect) and effect.skill not in entities.catalog:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{loc}/skill",
                        expected="CATALOG 里已登记的技能 id",
                        got=effect.skill,
                        rule_id="script_library.unknown_skill",
                    )
                )
            if isinstance(effect, GrantRelicEffect) and effect.relic not in entities.catalog:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{loc}/relic",
                        expected="CATALOG 里已登记的遗物技能 id",
                        got=effect.relic,
                        rule_id="script_library.unknown_relic",
                    )
                )
    return errors


def _event_pool_errors(
    file: str, position: int, pool: EventPool, entities: _Entities
) -> list[LoadError]:
    """事件池：``events`` 都要存在，且同池不重复（与 :func:`_skill_pool_errors` 同一口径）。

    每个引用点**恰好一条** ``LoadError``（票 35）：未知 id 优先报"引用不存在的东西"，
    不再同时报 ``duplicate_reference``——目标都不存在时"重复"没有独立语义。两张池表
    （技能池 / 事件池）因此对同一份坏数据报同样的条数，``loc`` 各自指向自己的引用方。
    """
    base = f"/event_pools/{position}"
    errors: list[LoadError] = []
    seen: dict[str, int] = {}
    for index, event_id in enumerate(pool.events):
        if event_id not in entities.events:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/events/{index}",
                    expected="events 里存在的事件 id",
                    got=event_id,
                    rule_id="script_library.unknown_event",
                )
            )
        elif event_id in seen:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/events/{index}",
                    expected="同一事件池里不重复的事件 id",
                    got=event_id,
                    rule_id="script_library.duplicate_reference",
                )
            )
        else:
            seen[event_id] = index
    return errors


def _global_skill_errors(
    file: str, position: int, skill: GlobalSkill
) -> list[LoadError]:
    """副本全局技能：三档区间必须齐全（v1 的效果由它们完全确定）。"""
    return _scale_errors(
        file, f"/global_skills/{position}/scale", skill.scale, require_all=True
    )


def _semantic_errors(file: str, library: ScriptLibrary) -> list[LoadError]:
    """结构通过之后的语义校验；**一次扫完**全部跨表引用（票 03 §6）。

    引用目标先建一次索引，再逐个引用方各扫一遍——所以断链会**一次报完**，
    且每条 ``loc`` 都指向**引用方**、``expected`` 点名被引用对象。
    """
    from sgs.skills import CATALOG

    errors: list[LoadError] = []
    entities = _Entities(
        rooms=_unique_index(errors, file, "rooms", library.rooms),
        templates=_unique_index(errors, file, "templates", library.templates),
        skill_pools=_unique_index(errors, file, "skill_pools", library.skill_pools),
        events=_unique_index(errors, file, "events", library.events),
        event_pools=_unique_index(errors, file, "event_pools", library.event_pools),
        global_skills=_unique_index(
            errors, file, "global_skills", library.global_skills
        ),
        heroes=_all_hero_ids(),
        catalog=CATALOG,
        band_counts=_band_counts(library.templates),
    )
    _unique_index(errors, file, "scripts", library.scripts)
    for position, script in enumerate(library.scripts):
        errors.extend(_script_errors(file, position, script, entities))
    for position, room in enumerate(library.rooms):
        errors.extend(_room_errors(file, position, room, entities))
    for position, template in enumerate(library.templates):
        errors.extend(_template_errors(file, position, template, entities))
    for position, pool in enumerate(library.skill_pools):
        errors.extend(_skill_pool_errors(file, position, pool, entities))
    for position, event in enumerate(library.events):
        errors.extend(_event_errors(file, position, event, entities))
    for position, pool in enumerate(library.event_pools):
        errors.extend(_event_pool_errors(file, position, pool, entities))
    for position, skill in enumerate(library.global_skills):
        errors.extend(_global_skill_errors(file, position, skill))
    return errors


def validate_script_library(
    raw: str | bytes | Mapping[str, Any], *, file: str = "<memory>"
) -> tuple[LoadError, ...]:
    """校验一份剧本库，**返回**全部 ``LoadError``（空 = 合法）。

    这是注册给运行期与编辑器的**同一个**函数（票 24 的校验面板与票 14 的严格加载同源）：
    它**从不**因为数据坏而抛——"拒绝"是调用方的策略（D30，见模块文档）。
    只有调用方写错参数（既不是 JSON 文本也不是映射）才会抛 ``TypeError``。
    """
    try:
        parsed = _parse_json(file, raw)
        library = _library_from_mapping(file, parsed)
    except DataLoadError as exc:
        return exc.errors
    return tuple(_semantic_errors(file, library))


def load_script_library(
    raw: str | bytes | Mapping[str, Any],
    *,
    file: str = "<memory>",
    strict: bool = True,
) -> ScriptLibrary:
    """加载一份剧本库。

    - ``strict=True``（缺省，**运行期**）：任何一段门的错误 ⇒ ``DataLoadError``（聚合全部）；
    - ``strict=False``（**编辑器**，票 24）：只放行语义错误——断链 / 问号池递归 / 座位数这类
      "删完引用体就会暂时不合法"的问题，否则编辑器删完东西就再也打不开库（D30）。
      版本与结构错误两档都拦：那两种状态下连模型都建不出来。
    """
    parsed = _parse_json(file, raw)
    library = _library_from_mapping(file, parsed)
    if strict:
        errors = _semantic_errors(file, library)
        if errors:
            raise DataLoadError(errors)
    return library


@lru_cache(maxsize=None)
def load_builtin_script_library() -> ScriptLibrary:
    """加载打包数据里的内置剧本库（``files/scripts.huangjin.json``）。

    纯函数式缓存：文件是**只读进料**（票 24 的"原件只读"），恒得同一份已校验的库
    （与 ``load_heroes`` / ``load_skill_values`` 同姿态）。
    """
    file = f"files/{SCRIPT_LIBRARY_FILENAME}"
    resource = files("sgs.data").joinpath("files", SCRIPT_LIBRARY_FILENAME)
    try:
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/",
                    expected="存在的剧本库文件",
                    got=SCRIPT_LIBRARY_FILENAME,
                    rule_id="script_library.not_found",
                )
            ]
        ) from exc
    return load_script_library(text, file=file)
