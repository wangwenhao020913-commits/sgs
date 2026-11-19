"""剧本编辑器的**库数据面**：用户库读写、校验面板、D30 删除 / 解除连线（M7 票 24）。

依据 **spec §4「删除与解除连线」**、**A8**、票 03 §9 的 D30 追加段与票 14 的加载器。
HTTP 路由在 :mod:`sgs_web.serve`（本模块只做「读文件 → 改模型 → 校验 → 写文件」），
与 :mod:`sgs_web.script_runs` 同一条分层：``src/sgs/`` 的规则一行都不搬进来。

**一个文件装整个库**（票 03 §7 / 票 24）：``runs/script-library.json``，与内置库**同 schema**
（不另立"可编辑"字段），写盘 ``.tmp`` → 轮换 ``.bak`` → 主档（与 ``hero-presets.json`` /
剧本存档同一条纪律）。内置库（``scripts.huangjin.json``）始终**只读**：它只是"用户库不存在
时的进料"与"导入的源"。

**校验面板与加载器同源**（票 24 的硬要求）：本模块直接调用
:func:`sgs.data.validate_script_library`（同一个函数对象，不是第二份实现），每次保存 / 每次
"校验"都返回逐条落到字段的 ``LoadError``。编辑器路径用
:func:`sgs.data.load_script_library` 的 ``strict=False`` 档读库——坏库能打开，面板红。

**D30 删除规则**（用户 2026-11-09 裁定，推翻票 03 §9 的「删被引用体 ⇒ 拒绝」）：

- **归属**（剧本 → 大节、大节 → 节点 / 边、房间 → 战斗 / 座位）：删除**级联**，永不拒绝。
  删节点带走它的入边 / 出边；删大节 = 从剧本的 ``layers`` 里摘掉；删剧本 = 整条消失。
- **共享引用**（节点 → 房间、战斗 → 模板 / 技能池 / 全局技能、事件池 → 事件、模板 → 技能池、
  房间 → 事件池 / 问号池…）：删除**照做**，被引用点**列出来但只警告**（``warnings`` 里 ``loc``
  指向**引用方**），引用方保留**悬空引用** ⇒ 校验面板立刻标红（``errors`` 与 ``warnings`` 同一条），
  编辑器**允许保存**这种库。运行期（:meth:`ScriptRuns.library` 的严格档）才拒绝启动该剧本。
- **连线可单独解除**：边是身份 ``(from, to)`` 的独立实体（重复边明确禁止——票 14 已判红，
  也免得"解一条"把同起终点的另一条误删）。

**revision 的读法**：库文件没有、也不能有 ``revision`` 字段（库根 ``extra="forbid"``，且票面
要求"一个文件装整个库"），故 ``revision`` = :func:`sgs.data.library_digest`（内容指纹）。
两个标签页各自保存时，后到的那个带的是旧指纹 ⇒ 409，与 ''逐次 +1 的整数''判据等价。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final, NoReturn

from sgs.data import (
    Battle,
    Edge,
    EnemyDraw,
    Event,
    EventPool,
    GlobalSkill,
    Layer,
    Node,
    Room,
    Script,
    ScriptLibrary,
    Seat,
    SkillPool,
    Template,
    LoadError,
    library_digest,
    load_builtin_script_library,
    load_script_library,
    script_digest,
    validate_script_library,
)

from .script_runs import SCRIPT_LIBRARY_FILENAME, SaveConflict, ScriptHttpError

__all__ = [
    "LIBRARY_TABLES",
    "SHARED_TABLES",
    "ScriptLibraryStore",
    "dump_errors",
    "dump_model",
]

LIBRARY_TABLES: Final = (
    "scripts",
    "rooms",
    "templates",
    "skill_pools",
    "events",
    "event_pools",
    "global_skills",
)
"""库里可增删改的全部**库级表**（票 03 §1 的实体清单；战斗 / 座位是房间的内嵌结构）。"""

SHARED_TABLES: Final = (
    "rooms",
    "templates",
    "skill_pools",
    "events",
    "event_pools",
    "global_skills",
)
"""**共享引用**的那几张表（票 03 §1 的"被谁引用"列）：剧本与大节不在内（归属）。"""

_MODEL: Final[dict[str, type]] = {
    "scripts": Script,
    "rooms": Room,
    "templates": Template,
    "skill_pools": SkillPool,
    "events": Event,
    "event_pools": EventPool,
    "global_skills": GlobalSkill,
    "layers": Layer,
    "nodes": Node,
    "edges": Edge,
    "battle": Battle,
    "seat": Seat,
}

_LABEL: Final[dict[str, str]] = {
    "scripts": "剧本",
    "rooms": "房间",
    "templates": "敌人模板",
    "skill_pools": "技能池",
    "events": "事件",
    "event_pools": "事件池",
    "global_skills": "全局技能",
    "layers": "大节",
    "nodes": "节点",
    "edges": "连线",
    "battle": "战斗",
    "seat": "座位",
}

_SEAT_LISTS: Final = ("allies", "enemies")
"""两侧座位列表的名字（票 03 §1；``player`` 是隐式 0 号座，不在数据里）。"""


# ---------------------------------------------------------------------------
# 形状：模型 → 磁盘 JSON（``by_alias=True``：``Edge.from`` 的键就是 ``from``）
# ---------------------------------------------------------------------------


def dump_model(value: Any) -> Any:
    """单个实体 / 一份库的磁盘形状（``from`` 别名、枚举取字符串值）。"""
    return value.model_dump(mode="json", by_alias=True)


def dump_errors(errors: Sequence[LoadError]) -> list[dict[str, str]]:
    """``LoadError`` → 线路上的五要素（不裁剪：面板要能落到字段）。"""
    return [
        {
            "file": error.file,
            "loc": error.loc,
            "expected": error.expected,
            "got": error.got,
            "rule_id": error.rule_id,
        }
        for error in errors
    ]


def _dump_text(library: ScriptLibrary) -> str:
    """库文件的字节形状：稳定键序 + 缩进（与 ``hero-presets.json`` 同一条书写纪律）。"""
    return json.dumps(
        dump_model(library), ensure_ascii=False, sort_keys=True, indent=2
    )


def _script_body(script: Script) -> dict[str, Any]:
    """剧本的**语义内容**（去掉 ``version``）：判"这次保存有没有真的改东西"。"""
    body = dump_model(script)
    body.pop("version", None)
    return body


def _bumped_version(previous: Script, current: Script) -> Script:
    """``script.version`` 由**服务端**管：内容变了 +1，没变就沿用旧值（票 03 §7）。"""
    if _script_body(previous) == _script_body(current):
        return current.model_copy(update={"version": previous.version})
    return current.model_copy(update={"version": previous.version + 1})


def _unique_id(base: str, taken: set[str], *, suffix: str) -> str:
    """在 ``taken`` 之外派生一个新 id：``<base><suffix>``、``…-2``、``…-3``…"""
    candidate = f"{base}{suffix}"
    counter = 2
    while candidate in taken:
        candidate = f"{base}{suffix}-{counter}"
        counter += 1
    return candidate


def _table(library: ScriptLibrary, kind: str) -> tuple[Any, ...]:
    return getattr(library, kind)


def _with_table(library: ScriptLibrary, kind: str, items: Sequence[Any]) -> ScriptLibrary:
    return library.model_copy(update={kind: tuple(items)})


def _index_of(items: Sequence[Any], ident: object) -> int | None:
    for index, item in enumerate(items):
        if item.id == ident:
            return index
    return None


# ---------------------------------------------------------------------------
# 归属：剧本 → 大节 → 节点 / 边；房间 → 战斗 → 座位
# ---------------------------------------------------------------------------


def _map_layers(script: Script, fn: Callable[[Layer], Layer]) -> Script:
    return script.model_copy(update={"layers": tuple(fn(layer) for layer in script.layers)})


def _map_nodes(layer: Layer, fn: Callable[[Node], Node]) -> Layer:
    return layer.model_copy(update={"nodes": tuple(fn(node) for node in layer.nodes)})


def _with_room_battle(
    library: ScriptLibrary, room_index: int, battle: Battle | None
) -> ScriptLibrary:
    rooms = list(library.rooms)
    rooms[room_index] = rooms[room_index].model_copy(update={"battle": battle})
    return _with_table(library, "rooms", rooms)


def _seat_list(battle: Battle, side: str) -> tuple[Seat, ...]:
    """取一侧座位（``enemies`` 是"按档抽"形态时没有座位列表 ⇒ 400）。"""
    if side not in _SEAT_LISTS:
        raise ValueError(f"座位侧只能是 {_SEAT_LISTS}：{side!r}")
    value = getattr(battle, side)
    if isinstance(value, EnemyDraw):
        raise ValueError("这一场敌人是**按档抽**的，没有座位列表可编辑")
    return value


def _with_seats(battle: Battle, side: str, seats: Sequence[Seat]) -> Battle:
    return battle.model_copy(update={side: tuple(seats)})


# ---------------------------------------------------------------------------
# 重命名：改**稳定 id** 时把引用点一起改写（票 24：「重命名保引用」）
#
# 每张共享表的引用点都是显式的（不靠"猜键名"）：房间被节点 + 问号池引用、模板被座位引用、
# 技能池被模板引用、事件被事件池引用、事件池被房间引用、全局技能被战斗引用。
# ---------------------------------------------------------------------------


def _rename_script(library: ScriptLibrary, old: str, new: str) -> ScriptLibrary:
    """剧本 id 只被存档引用（库内无引用点）。"""
    scripts = tuple(
        script.model_copy(update={"id": new}) if script.id == old else script
        for script in library.scripts
    )
    return _with_table(library, "scripts", scripts)


def _rename_room(library: ScriptLibrary, old: str, new: str) -> ScriptLibrary:
    """节点 → 房间、问号池 → 房间两处引用。"""
    scripts = tuple(
        _map_layers(
            script,
            lambda layer: _map_nodes(
                layer,
                lambda node: node.model_copy(update={"room": new})
                if node.room == old
                else node,
            ),
        )
        for script in library.scripts
    )
    rooms = []
    for room in library.rooms:
        if room.id == old:
            room = room.model_copy(update={"id": new})
        if room.pool is not None:
            room = room.model_copy(
                update={
                    "pool": tuple(
                        entry.model_copy(update={"room": new})
                        if entry.room == old
                        else entry
                        for entry in room.pool
                    )
                }
            )
        rooms.append(room)
    return library.model_copy(update={"scripts": scripts, "rooms": tuple(rooms)})


def _rename_template(library: ScriptLibrary, old: str, new: str) -> ScriptLibrary:
    """座位 → 模板（固定座位；按档抽按 ``band`` 标签，不是模板 id）。"""

    def seat(value: Seat) -> Seat:
        return (
            value.model_copy(update={"template": new})
            if value.template == old
            else value
        )

    def battle(value: Battle) -> Battle:
        enemies = value.enemies
        if not isinstance(enemies, EnemyDraw):
            enemies = tuple(seat(item) for item in enemies)
        return value.model_copy(
            update={
                "allies": tuple(seat(item) for item in value.allies),
                "enemies": enemies,
            }
        )

    rooms = tuple(
        room.model_copy(update={"battle": battle(room.battle)})
        if room.battle is not None
        else room
        for room in library.rooms
    )
    templates = tuple(
        template.model_copy(update={"id": new}) if template.id == old else template
        for template in library.templates
    )
    return library.model_copy(update={"rooms": rooms, "templates": templates})


def _rename_skill_pool(library: ScriptLibrary, old: str, new: str) -> ScriptLibrary:
    """模板 → 技能池（``random.pool``）。"""
    templates = tuple(
        template.model_copy(
            update={"random": template.random.model_copy(update={"pool": new})}
        )
        if template.random.pool == old
        else template
        for template in library.templates
    )
    pools = tuple(
        pool.model_copy(update={"id": new}) if pool.id == old else pool
        for pool in library.skill_pools
    )
    return library.model_copy(update={"templates": templates, "skill_pools": pools})


def _rename_event(library: ScriptLibrary, old: str, new: str) -> ScriptLibrary:
    """事件池 → 事件。"""
    pools = tuple(
        pool.model_copy(
            update={
                "events": tuple(new if event == old else event for event in pool.events)
            }
        )
        for pool in library.event_pools
    )
    events = tuple(
        event.model_copy(update={"id": new}) if event.id == old else event
        for event in library.events
    )
    return library.model_copy(update={"event_pools": pools, "events": events})


def _rename_event_pool(library: ScriptLibrary, old: str, new: str) -> ScriptLibrary:
    """房间 → 事件池。"""
    rooms = tuple(
        room.model_copy(update={"event_pool": new})
        if room.event_pool == old
        else room
        for room in library.rooms
    )
    pools = tuple(
        pool.model_copy(update={"id": new}) if pool.id == old else pool
        for pool in library.event_pools
    )
    return library.model_copy(update={"rooms": rooms, "event_pools": pools})


def _rename_global_skill(library: ScriptLibrary, old: str, new: str) -> ScriptLibrary:
    """战斗 → 全局技能（``global_skills[].skill``）。"""

    def battle(value: Battle) -> Battle:
        return value.model_copy(
            update={
                "global_skills": tuple(
                    ref.model_copy(update={"skill": new}) if ref.skill == old else ref
                    for ref in value.global_skills
                )
            }
        )

    rooms = tuple(
        room.model_copy(update={"battle": battle(room.battle)})
        if room.battle is not None
        else room
        for room in library.rooms
    )
    skills = tuple(
        skill.model_copy(update={"id": new}) if skill.id == old else skill
        for skill in library.global_skills
    )
    return library.model_copy(update={"rooms": rooms, "global_skills": skills})


_RENAMERS: Final[dict[str, Callable[[ScriptLibrary, str, str], ScriptLibrary]]] = {
    "scripts": _rename_script,
    "rooms": _rename_room,
    "templates": _rename_template,
    "skill_pools": _rename_skill_pool,
    "events": _rename_event,
    "event_pools": _rename_event_pool,
    "global_skills": _rename_global_skill,
}


# ---------------------------------------------------------------------------
# 库的读写 + 端点语义
# ---------------------------------------------------------------------------


class ScriptLibraryStore:
    """``runs/script-library.json`` 这一份用户库的全部读写与端点语义。

    ``ScriptRuns`` 的严格档（:meth:`sgs_web.script_runs.ScriptRuns.library`）是**运行期**
    读库的那条路；本类走 ``strict=False``（编辑器档）——两条路共用同一份校验函数，区别只在
    "拿到错误清单之后拒不拒"（D30）。
    """

    def __init__(self, *, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)
        # 一把进程内锁串行化「读库 → 改 → 写」（与 ScriptRuns 同一条纪律：跨进程只靠 revision）。
        self._lock = threading.RLock()

    # -- 路径 ---------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self.runs_dir / SCRIPT_LIBRARY_FILENAME

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.bak")

    @property
    def tmp_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.tmp")

    @property
    def file(self) -> str:
        """错误清单里的 ``file`` 标签（与运行期严格档**同一串**，两边才能逐字段比对）。"""
        return str(self.path)

    # -- 读写 ---------------------------------------------------------------

    def _raw(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def _load(self, raw: bytes | None) -> ScriptLibrary:
        """用户库（编辑器宽松档）；文件不在 ⇒ 内置库（只读进料）。"""
        if raw is None:
            return load_builtin_script_library()
        return load_script_library(raw, file=self.file, strict=False)

    def library(self) -> ScriptLibrary:
        with self._lock:
            return self._load(self._raw())

    def _errors(self, library: ScriptLibrary) -> tuple[LoadError, ...]:
        """唯一校验入口：加载器那一个函数（``file`` 与本类钉住的那串一致）。"""
        return validate_script_library(dump_model(library), file=self.file)

    def _write(self, text: str) -> None:
        """整份落盘：``.tmp`` → 轮换 ``.bak`` → 主档（与剧本存档同一条两次 rename 纪律）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_path.write_text(text, encoding="utf-8")
        if self.path.exists():
            os.replace(self.path, self.backup_path)
        os.replace(self.tmp_path, self.path)

    # -- 返回体 -------------------------------------------------------------

    def _envelope(
        self,
        library: ScriptLibrary,
        *,
        errors: Sequence[LoadError],
        warnings: Sequence[LoadError] = (),
        entity: Any = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "revision": library_digest(library),
            "source": "user",
            "library": dump_model(library),
            "errors": dump_errors(errors),
            "warnings": dump_errors(warnings),
            "entity": None if entity is None else dump_model(entity),
        }
        if extra:
            payload.update(extra)
        return payload

    def catalog(self) -> dict[str, Any]:
        """``GET /api/script-library``：整份库 + revision + 面板（编辑器一次读全）。"""
        with self._lock:
            raw = self._raw()
            library = self._load(raw)
            return {
                "revision": library_digest(library),
                "source": "builtin" if raw is None else "user",
                "library": dump_model(library),
                "errors": dump_errors(self._errors(library)),
            }

    def validate(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /api/script-library/validate``：候选库（缺省 = 当前库）的错误清单。"""
        raw = body.get("library")
        with self._lock:
            if raw is None:
                library = self._load(self._raw())
                revision = library_digest(library)
                errors = self._errors(library)
            else:
                if not isinstance(raw, Mapping):
                    raise ValueError("validate 的 library 必须是 JSON 对象")
                errors = tuple(validate_script_library(raw, file=self.file))
                revision = library_digest(self._load(self._raw()))
            return {
                "ok": not errors,
                "revision": revision,
                "errors": dump_errors(errors),
            }

    # -- 写：一把锁里「查 revision → 改 → 版本 +1 → 校验 → 落盘」 -------------

    def _conflict(self, library: ScriptLibrary, message: str) -> NoReturn:
        payload = {
            "revision": library_digest(library),
            "library": dump_model(library),
            "errors": dump_errors(self._errors(library)),
            "error": message,
        }
        raise SaveConflict(payload)

    def _require_revision(
        self, body: Mapping[str, Any], current: ScriptLibrary, raw: bytes | None
    ) -> None:
        """乐观锁：用户库已存在 ⇒ 必须带**当前** revision（两个标签页不许互相覆盖）。"""
        expected = body.get("expected_revision")
        current_revision = library_digest(current)
        if raw is None and expected is None:
            return  # 用户库还没落过盘：没有可覆盖的东西
        if not isinstance(expected, str) or expected != current_revision:
            self._conflict(
                current,
                "revision 已过期：请按当前 revision 重取剧本库"
                if expected is not None
                else "写库必须带 expected_revision",
            )

    def _commit(
        self,
        body: Mapping[str, Any],
        change: Callable[[ScriptLibrary], tuple[ScriptLibrary, Any]],
        *,
        warnings_for: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """一次写入：先挡 revision，再改模型、自动 +1 版本、校验（允许语义错）、落盘。

        ``warnings_for`` = 本次删掉的实体 id：``warnings`` 只收"新出现的、点名它的"错误
        （D30 的「被引用点列出来但只警告」，``loc`` 指向引用方）。``None`` = 本次是**归属**
        删除（级联），新出现的错误全部算警告。
        """
        with self._lock:
            raw = self._raw()
            current = self._load(raw)
            self._require_revision(body, current, raw)
            before = set(self._errors(current))
            changed, entity = change(current)
            previous_scripts = {script.id: script for script in current.scripts}
            scripts = tuple(
                _bumped_version(previous_scripts[script.id], script)
                if script.id in previous_scripts
                else script
                for script in changed.scripts
            )
            changed = changed.model_copy(update={"scripts": scripts})
            if isinstance(entity, Script):
                # 返回体里的 entity 也要是**落盘后**的那份（版本 +1 发生在上面）。
                entity = next(
                    (item for item in scripts if item.id == entity.id), entity
                )
            text = _dump_text(changed)
            # 版本 / 结构门：写出去的库必须**读得回来**（语义坏可以，模型建不出来不行）。
            # 这一道同时也兜住 ``model_copy(update=…)`` 绕过的字段校验。
            load_script_library(text, file=self.file, strict=False)
            errors = tuple(validate_script_library(text, file=self.file))
            warnings = [
                error
                for error in errors
                if error not in before
                and (warnings_for is None or warnings_for in error.got)
            ]
            self._write(text)
            return self._envelope(
                changed, errors=errors, warnings=warnings, entity=entity, extra=extra
            )

    # -- 整库替换 -----------------------------------------------------------

    def replace(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``PUT /api/script-library``：整份库写回（**允许**语义坏，不许版本 / 结构坏）。"""
        raw = body.get("library")
        if not isinstance(raw, Mapping):
            raise ValueError("PUT /api/script-library 需要 library 对象")

        def change(_current: ScriptLibrary) -> tuple[ScriptLibrary, None]:
            # 版本 / 结构过不去 ⇒ DataLoadError（400，文件不动）。语义错放行（D30）。
            return load_script_library(raw, file=self.file, strict=False), None

        return self._commit(body, change)

    # -- 库级表的 CRUD（剧本 + 六个共享表） ---------------------------------

    @staticmethod
    def _find(kind: str, items: Sequence[Any], ident: object) -> tuple[int, Any]:
        """按稳定 id 找一条；找不到 ⇒ 404（与 ``/api/script-runs`` 的寻址语义一致）。"""
        index = _index_of(items, ident)
        if index is None:
            raise ScriptHttpError(404, {"error": f"{_LABEL[kind]}里没有 {ident!r}"})
        return index, items[index]

    @staticmethod
    def _entity_body(kind: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        value = body.get("entity")
        if not isinstance(value, Mapping):
            raise ValueError(f"写{_LABEL[kind]}需要 entity 对象：{value!r}")
        return value

    @staticmethod
    def _model_entity(kind: str, body: Mapping[str, Any]) -> Any:
        """从 ``{"entity": {...}}`` 里取一条并按模型校验（结构非法 ⇒ 400）。"""
        return ScriptLibraryStore._model(kind, ScriptLibraryStore._entity_body(kind, body))

    @staticmethod
    def _model(kind: str, raw: Mapping[str, Any]) -> Any:
        """按模型校验一条实体的 JSON（字段非法 ⇒ 400：结构错不像 D30 那样放行）。"""
        try:
            return _MODEL[kind].model_validate(raw)
        except ValueError as error:  # pydantic.ValidationError 是 ValueError 的子类
            raise ValueError(f"{_LABEL[kind]}字段非法：{error}") from error

    def list_entities(self, kind: str) -> dict[str, Any]:
        """``GET /api/script-library/<表>``：一张表的全量（编辑器列表页的直接进料）。"""
        with self._lock:
            library = self._load(self._raw())
            return {
                "revision": library_digest(library),
                "items": [dump_model(item) for item in _table(library, kind)],
            }

    def get_entity(self, kind: str, ident: str) -> dict[str, Any]:
        """``GET /api/script-library/<表>/<id>``：单条（剧本另带现算的 ``digest``）。"""
        with self._lock:
            library = self._load(self._raw())
            _, entity = self._find(kind, _table(library, kind), ident)
            payload: dict[str, Any] = {
                "revision": library_digest(library),
                "entity": dump_model(entity),
            }
            if kind == "scripts":
                payload["digest"] = script_digest(entity)
            return payload

    def create_entity(self, kind: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /api/script-library/<表>``：新建一条（id 撞车 ⇒ 409）。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Any]:
            raw = dict(self._entity_body(kind, body))
            if kind == "scripts":
                raw.setdefault("version", 1)
            entity = self._model(kind, raw)
            items = _table(library, kind)
            if _index_of(items, entity.id) is not None:
                raise ScriptHttpError(
                    409, {"error": f"{_LABEL[kind]} id 已被占用：{entity.id}"}
                )
            return _with_table(library, kind, (*items, entity)), entity

        return self._commit(body, change)

    def update_entity(
        self, kind: str, ident: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """``PUT /api/script-library/<表>/<id>``：整条替换（改 id 请走 ``rename``）。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Any]:
            index, _ = self._find(kind, _table(library, kind), ident)
            entity = self._model(kind, self._entity_body(kind, body))
            if entity.id != ident:
                raise ValueError(
                    f"改 id 请走 rename（引用点要一起改写）：{ident!r} → {entity.id!r}"
                )
            items = list(_table(library, kind))
            items[index] = entity
            return _with_table(library, kind, items), entity

        return self._commit(body, change)

    def delete_entity(
        self, kind: str, ident: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """``DELETE /api/script-library/<表>/<id>``：D30 —— 删**照做**，被引用点只警告。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, None]:
            index, _ = self._find(kind, _table(library, kind), ident)
            items = list(_table(library, kind))
            items.pop(index)
            return _with_table(library, kind, items), None

        return self._commit(body, change, warnings_for=ident)

    def references(self, kind: str, ident: str) -> dict[str, Any]:
        """``GET …/<表>/<id>/references``：**删之前**看"删了会悬空哪些引用点"。

        D30 的确认框要列出引用点（票 25），判据与删除后的 ``warnings`` **同一条**：把实体
        从库里摘掉、跑同一份校验器、只看"新出现的、点名这个 id 的"错误（``loc`` 指向引用方）。
        它只读：不落盘、不动 revision。
        """
        with self._lock:
            library = self._load(self._raw())
            index, _ = self._find(kind, _table(library, kind), ident)
            before = set(self._errors(library))
            items = list(_table(library, kind))
            items.pop(index)
            after = self._errors(_with_table(library, kind, items))
            dangling = [
                error for error in after if error not in before and ident in error.got
            ]
            return {
                "revision": library_digest(library),
                "references": dump_errors(dangling),
            }

    def clone_entity(
        self, kind: str, ident: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """``POST /api/script-library/<表>/<id>/clone``：另存为副本（**重写 id**）。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Any]:
            _, source = self._find(kind, _table(library, kind), ident)
            items = _table(library, kind)
            taken = {item.id for item in items}
            new_id = body.get("new_id")
            if new_id is None:
                new_id = _unique_id(source.id, taken, suffix="-copy")
            elif not isinstance(new_id, str) or not new_id:
                raise ValueError(f"new_id 必须是非空字符串：{new_id!r}")
            elif new_id in taken:
                raise ScriptHttpError(
                    409, {"error": f"{_LABEL[kind]} id 已被占用：{new_id}"}
                )
            copy = _MODEL[kind].model_validate(
                {
                    **dump_model(source),
                    **self._clone_overrides(source, body),
                    "id": new_id,
                }
            )
            return _with_table(library, kind, (*items, copy)), copy

        return self._commit(body, change)

    @staticmethod
    def _clone_overrides(source: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        """副本的字段覆盖（``new_name`` / ``entity``）；id 由调用方最后钉死。"""
        overrides = body.get("entity")
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, Mapping):
            raise ValueError(f"clone 的 entity 必须是 JSON 对象：{overrides!r}")
        updates = {key: value for key, value in overrides.items() if key != "id"}
        if "name" in type(source).model_fields and "name" not in updates:
            new_name = body.get("new_name")
            updates["name"] = new_name if isinstance(new_name, str) else f"{source.name}（副本）"
        return updates

    def rename_entity(
        self, kind: str, ident: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """``POST /api/script-library/<表>/<id>/rename``：改名**保引用**。

        ``new_id`` = 改稳定 id：:data:`_RENAMERS` 把全部引用点一起改写（断链数为 0）；
        ``name`` = 只改显示名（当然也不动引用）。两者可同时给。
        """

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Any]:
            _, source = self._find(kind, _table(library, kind), ident)
            new_id = body.get("new_id")
            name = body.get("name")
            if new_id is None and name is None:
                raise ValueError("rename 需要 new_id 或 name")
            if new_id is not None:
                if not isinstance(new_id, str) or not new_id:
                    raise ValueError(f"new_id 必须是非空字符串：{new_id!r}")
                if new_id != ident:
                    if _index_of(_table(library, kind), new_id) is not None:
                        raise ScriptHttpError(
                            409, {"error": f"{_LABEL[kind]} id 已被占用：{new_id}"}
                        )
                    library = _RENAMERS[kind](library, ident, new_id)
                    _, source = self._find(kind, _table(library, kind), new_id)
            if name is not None:
                if not isinstance(name, str):
                    raise ValueError(f"name 必须是字符串：{name!r}")
                source = _MODEL[kind].model_validate(
                    {**dump_model(source), "name": name}
                )
                index, _ = self._find(kind, _table(library, kind), source.id)
                items = list(_table(library, kind))
                items[index] = source
                library = _with_table(library, kind, items)
            return library, source

        return self._commit(body, change)

    # -- 剧本归属：大节 / 节点 / 边 -----------------------------------------

    @staticmethod
    def _script_at(library: ScriptLibrary, script_id: str) -> tuple[int, Script]:
        index = _index_of(library.scripts, script_id)
        if index is None:
            raise ScriptHttpError(404, {"error": f"剧本库里没有剧本 {script_id!r}"})
        return index, library.scripts[index]

    @staticmethod
    def _layer_at(script: Script, layer_id: str) -> tuple[int, Layer]:
        index = _index_of(script.layers, layer_id)
        if index is None:
            raise ScriptHttpError(
                404, {"error": f"剧本 {script.id!r} 里没有大节 {layer_id!r}"}
            )
        return index, script.layers[index]

    @staticmethod
    def _node_at(layer: Layer, node_id: str) -> tuple[int, Node]:
        index = _index_of(layer.nodes, node_id)
        if index is None:
            raise ScriptHttpError(
                404, {"error": f"大节 {layer.id!r} 里没有节点 {node_id!r}"}
            )
        return index, layer.nodes[index]

    @staticmethod
    def _edge_at(layer: Layer, source: str, target: str) -> tuple[int, Edge]:
        for index, edge in enumerate(layer.edges):
            if edge.from_ == source and edge.to == target:
                return index, edge
        raise ScriptHttpError(
            404, {"error": f"大节 {layer.id!r} 里没有连线 {source} → {target}"}
        )

    @staticmethod
    def _with_layer(
        library: ScriptLibrary, script_index: int, layer_index: int, layer: Layer
    ) -> ScriptLibrary:
        scripts = list(library.scripts)
        layers = list(scripts[script_index].layers)
        layers[layer_index] = layer
        scripts[script_index] = scripts[script_index].model_copy(
            update={"layers": tuple(layers)}
        )
        return library.model_copy(update={"scripts": tuple(scripts)})

    def list_layers(self, script_id: str) -> dict[str, Any]:
        with self._lock:
            library = self._load(self._raw())
            _, script = self._script_at(library, script_id)
            return {
                "revision": library_digest(library),
                "items": [dump_model(layer) for layer in script.layers],
            }

    def get_layer(self, script_id: str, layer_id: str) -> dict[str, Any]:
        with self._lock:
            library = self._load(self._raw())
            _, script = self._script_at(library, script_id)
            _, layer = self._layer_at(script, layer_id)
            return {
                "revision": library_digest(library),
                "entity": dump_model(layer),
            }

    def create_layer(self, script_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST …/layers``：新大节挂到剧本 ``layers`` 末尾（归属：不查引用）。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Layer]:
            script_index, script = self._script_at(library, script_id)
            layer = self._model_entity("layers", body)
            if _index_of(script.layers, layer.id) is not None:
                raise ScriptHttpError(
                    409, {"error": f"剧本里已有大节 {layer.id!r}"}
                )
            scripts = list(library.scripts)
            scripts[script_index] = script.model_copy(
                update={"layers": (*script.layers, layer)}
            )
            return library.model_copy(update={"scripts": tuple(scripts)}), layer

        return self._commit(body, change)

    def update_layer(
        self, script_id: str, layer_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Layer]:
            script_index, script = self._script_at(library, script_id)
            layer_index, _ = self._layer_at(script, layer_id)
            layer = self._model_entity("layers", body)
            if layer.id != layer_id:
                raise ValueError("改 id 请走 rename（边的端点要一起改写）")
            return self._with_layer(library, script_index, layer_index, layer), layer

        return self._commit(body, change)

    def delete_layer(
        self, script_id: str, layer_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """D30 归属删除：从剧本 ``layers`` 里摘掉，**永不拒绝**。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, None]:
            script_index, script = self._script_at(library, script_id)
            layer_index, _ = self._layer_at(script, layer_id)
            scripts = list(library.scripts)
            layers = list(script.layers)
            layers.pop(layer_index)
            scripts[script_index] = script.model_copy(update={"layers": tuple(layers)})
            return library.model_copy(update={"scripts": tuple(scripts)}), None

        return self._commit(body, change)

    def clone_layer(
        self, script_id: str, layer_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Layer]:
            script_index, script = self._script_at(library, script_id)
            _, source = self._layer_at(script, layer_id)
            taken = {layer.id for layer in script.layers}
            new_id = body.get("new_id")
            if new_id is None:
                new_id = _unique_id(source.id, taken, suffix="-copy")
            elif not isinstance(new_id, str) or not new_id:
                raise ValueError(f"new_id 必须是非空字符串：{new_id!r}")
            elif new_id in taken:
                raise ScriptHttpError(409, {"error": f"剧本里已有大节 {new_id}"})
            copy = Layer.model_validate(
                {
                    **dump_model(source),
                    **self._clone_overrides(source, body),
                    "id": new_id,
                }
            )
            scripts = list(library.scripts)
            scripts[script_index] = script.model_copy(
                update={"layers": (*script.layers, copy)}
            )
            return library.model_copy(update={"scripts": tuple(scripts)}), copy

        return self._commit(body, change)

    def rename_layer(
        self, script_id: str, layer_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Layer]:
            script_index, script = self._script_at(library, script_id)
            layer_index, source = self._layer_at(script, layer_id)
            new_id = body.get("new_id")
            if new_id is not None and new_id != layer_id:
                if not isinstance(new_id, str) or not new_id:
                    raise ValueError(f"new_id 必须是非空字符串：{new_id!r}")
                if _index_of(script.layers, new_id) is not None:
                    raise ScriptHttpError(409, {"error": f"剧本里已有大节 {new_id}"})
                source = source.model_copy(update={"id": new_id})
                layer_index = _index_of(script.layers, layer_id)
            name = body.get("name")
            if name is not None:
                if not isinstance(name, str):
                    raise ValueError(f"name 必须是字符串：{name!r}")
                source = source.model_copy(update={"name": name})
            if new_id is None and name is None:
                raise ValueError("rename 需要 new_id 或 name")
            return self._with_layer(library, script_index, layer_index, source), source

        return self._commit(body, change)

    # -- 节点 ---------------------------------------------------------------

    def list_nodes(self, script_id: str, layer_id: str) -> dict[str, Any]:
        with self._lock:
            library = self._load(self._raw())
            _, script = self._script_at(library, script_id)
            _, layer = self._layer_at(script, layer_id)
            return {
                "revision": library_digest(library),
                "items": [dump_model(node) for node in layer.nodes],
            }

    def get_node(self, script_id: str, layer_id: str, node_id: str) -> dict[str, Any]:
        with self._lock:
            library = self._load(self._raw())
            _, script = self._script_at(library, script_id)
            _, layer = self._layer_at(script, layer_id)
            _, node = self._node_at(layer, node_id)
            return {
                "revision": library_digest(library),
                "entity": dump_model(node),
            }

    def create_node(
        self, script_id: str, layer_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Node]:
            script_index, script = self._script_at(library, script_id)
            layer_index, layer = self._layer_at(script, layer_id)
            node = self._model_entity("nodes", body)
            if _index_of(layer.nodes, node.id) is not None:
                raise ScriptHttpError(409, {"error": f"大节里已有节点 {node.id!r}"})
            layer = layer.model_copy(update={"nodes": (*layer.nodes, node)})
            return self._with_layer(library, script_index, layer_index, layer), node

        return self._commit(body, change)

    def update_node(
        self, script_id: str, layer_id: str, node_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Node]:
            script_index, script = self._script_at(library, script_id)
            layer_index, layer = self._layer_at(script, layer_id)
            index, _ = self._node_at(layer, node_id)
            node = self._model_entity("nodes", body)
            if node.id != node_id:
                raise ValueError("改 id 请走 rename（连线端点要一起改写）")
            nodes = list(layer.nodes)
            nodes[index] = node
            layer = layer.model_copy(update={"nodes": tuple(nodes)})
            return self._with_layer(library, script_index, layer_index, layer), node

        return self._commit(body, change)

    def delete_node(
        self, script_id: str, layer_id: str, node_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """D30 归属删除：节点 + 它的**入边 / 出边**一起删；别的边一条不动。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, None]:
            script_index, script = self._script_at(library, script_id)
            layer_index, layer = self._layer_at(script, layer_id)
            index, _ = self._node_at(layer, node_id)
            nodes = list(layer.nodes)
            nodes.pop(index)
            edges = tuple(
                edge
                for edge in layer.edges
                if edge.from_ != node_id and edge.to != node_id
            )
            layer = layer.model_copy(update={"nodes": tuple(nodes), "edges": edges})
            return self._with_layer(library, script_index, layer_index, layer), None

        return self._commit(body, change)

    def clone_node(
        self, script_id: str, layer_id: str, node_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """另存为节点副本：**只复制节点本身，不复制连线**（连线是独立的归属物）。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Node]:
            script_index, script = self._script_at(library, script_id)
            layer_index, layer = self._layer_at(script, layer_id)
            _, source = self._node_at(layer, node_id)
            taken = {node.id for node in layer.nodes}
            new_id = body.get("new_id")
            if new_id is None:
                new_id = _unique_id(source.id, taken, suffix="-copy")
            elif not isinstance(new_id, str) or not new_id:
                raise ValueError(f"new_id 必须是非空字符串：{new_id!r}")
            elif new_id in taken:
                raise ScriptHttpError(409, {"error": f"大节里已有节点 {new_id}"})
            overrides = body.get("entity") or {}
            if not isinstance(overrides, Mapping):
                raise ValueError(f"clone 的 entity 必须是 JSON 对象：{overrides!r}")
            copy = Node.model_validate(
                {
                    **dump_model(source),
                    **{key: value for key, value in overrides.items() if key != "id"},
                    "id": new_id,
                }
            )
            layer = layer.model_copy(update={"nodes": (*layer.nodes, copy)})
            return self._with_layer(library, script_index, layer_index, layer), copy

        return self._commit(body, change)

    def rename_node(
        self, script_id: str, layer_id: str, node_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """节点改名：同一大节里所有引用它的**连线端点**一起改写（保引用）。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Node]:
            script_index, script = self._script_at(library, script_id)
            layer_index, layer = self._layer_at(script, layer_id)
            _, source = self._node_at(layer, node_id)
            new_id = body.get("new_id")
            if not isinstance(new_id, str) or not new_id:
                raise ValueError(f"节点没有名字字段，rename 只收 new_id：{new_id!r}")
            if new_id != node_id:
                if _index_of(layer.nodes, new_id) is not None:
                    raise ScriptHttpError(409, {"error": f"大节里已有节点 {new_id}"})
                layer = layer.model_copy(
                    update={
                        "nodes": tuple(
                            node.model_copy(update={"id": new_id})
                            if node.id == node_id
                            else node
                            for node in layer.nodes
                        ),
                        "edges": tuple(
                            edge.model_copy(
                                update={
                                    "from_": new_id if edge.from_ == node_id else edge.from_,
                                    "to": new_id if edge.to == node_id else edge.to,
                                }
                            )
                            for edge in layer.edges
                        ),
                    }
                )
                _, source = self._node_at(layer, new_id)
            return self._with_layer(library, script_index, layer_index, layer), source

        return self._commit(body, change)

    # -- 边（解除连线） -----------------------------------------------------

    def list_edges(self, script_id: str, layer_id: str) -> dict[str, Any]:
        with self._lock:
            library = self._load(self._raw())
            _, script = self._script_at(library, script_id)
            _, layer = self._layer_at(script, layer_id)
            return {
                "revision": library_digest(library),
                "items": [dump_model(edge) for edge in layer.edges],
            }

    def get_edge(
        self, script_id: str, layer_id: str, source: str, target: str
    ) -> dict[str, Any]:
        with self._lock:
            library = self._load(self._raw())
            _, script = self._script_at(library, script_id)
            _, layer = self._layer_at(script, layer_id)
            _, edge = self._edge_at(layer, source, target)
            return {
                "revision": library_digest(library),
                "entity": dump_model(edge),
            }

    def create_edge(
        self, script_id: str, layer_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """画边（与解除连线对称）：同一对 ``(from, to)`` **明确禁止**重复。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Edge]:
            script_index, script = self._script_at(library, script_id)
            layer_index, layer = self._layer_at(script, layer_id)
            edge = self._model_entity("edges", body)
            if any(
                item.from_ == edge.from_ and item.to == edge.to for item in layer.edges
            ):
                raise ScriptHttpError(
                    409,
                    {"error": f"连线已存在：{edge.from_} → {edge.to}（同一对节点只画一次）"},
                )
            layer = layer.model_copy(update={"edges": (*layer.edges, edge)})
            return self._with_layer(library, script_index, layer_index, layer), edge

        return self._commit(body, change)

    def update_edge(
        self,
        script_id: str,
        layer_id: str,
        source: str,
        target: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        """改一条边的条件（``requires``）；端点就是它的身份，不许顺手改。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Edge]:
            script_index, script = self._script_at(library, script_id)
            layer_index, layer = self._layer_at(script, layer_id)
            index, _ = self._edge_at(layer, source, target)
            raw = dict(self._entity_body("edges", body))
            raw.setdefault("from", source)
            raw.setdefault("to", target)
            edge = self._model("edges", raw)
            if edge.from_ != source or edge.to != target:
                raise ValueError(
                    f"边的端点就是它的身份：{source} → {target}（删了重画才换端点）"
                )
            edges = list(layer.edges)
            edges[index] = edge
            layer = layer.model_copy(update={"edges": tuple(edges)})
            return self._with_layer(library, script_index, layer_index, layer), edge

        return self._commit(body, change)

    def delete_edge(
        self, script_id: str, layer_id: str, source: str, target: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """解除连线：只摘掉这一条（身份 = ``(from, to)``），别的边一条不动。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, None]:
            script_index, script = self._script_at(library, script_id)
            layer_index, layer = self._layer_at(script, layer_id)
            index, _ = self._edge_at(layer, source, target)
            edges = list(layer.edges)
            edges.pop(index)
            layer = layer.model_copy(update={"edges": tuple(edges)})
            return self._with_layer(library, script_index, layer_index, layer), None

        return self._commit(body, change)

    # -- 房间归属：战斗与座位（房间 → 战斗 → 座位） -------------------------

    @staticmethod
    def _room_at(library: ScriptLibrary, room_id: str) -> tuple[int, Room]:
        index = _index_of(library.rooms, room_id)
        if index is None:
            raise ScriptHttpError(404, {"error": f"剧本库里没有房间 {room_id!r}"})
        return index, library.rooms[index]

    @staticmethod
    def _battle_at(library: ScriptLibrary, room_id: str) -> tuple[int, Battle]:
        index, room = ScriptLibraryStore._room_at(library, room_id)
        if room.battle is None:
            raise ScriptHttpError(404, {"error": f"房间 {room_id!r} 没有战斗编排"})
        return index, room.battle

    def get_battle(self, room_id: str) -> dict[str, Any]:
        with self._lock:
            library = self._load(self._raw())
            _, room = self._room_at(library, room_id)
            return {
                "revision": library_digest(library),
                "entity": None if room.battle is None else dump_model(room.battle),
            }

    def set_battle(self, room_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """``PUT rooms/<rid>/battle``：整份战斗编排（房间 ``kind`` 决定它该不该在）。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Battle]:
            room_index, _ = self._room_at(library, room_id)
            battle = self._model("battle", self._entity_body("battle", body))
            return _with_room_battle(library, room_index, battle), battle

        return self._commit(body, change)

    def delete_battle(self, room_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """删战斗 = 房间的 ``battle`` 置空（剧情 / 战斗房随即在面板上标红——D30 允许保存）。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, None]:
            room_index, _ = self._battle_at(library, room_id)
            return _with_room_battle(library, room_index, None), None

        return self._commit(body, change)

    def list_seats(self, room_id: str, side: str) -> dict[str, Any]:
        with self._lock:
            library = self._load(self._raw())
            _, battle = self._battle_at(library, room_id)
            return {
                "revision": library_digest(library),
                "items": [dump_model(seat) for seat in _seat_list(battle, side)],
            }

    def create_seat(
        self, room_id: str, side: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """``POST rooms/<rid>/battle/<side>``：往该侧座位列表**末尾**加一座。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Seat]:
            room_index, battle = self._battle_at(library, room_id)
            seat = self._model_entity("seat", body)
            seats = (*_seat_list(battle, side), seat)
            return (
                _with_room_battle(library, room_index, _with_seats(battle, side, seats)),
                seat,
            )

        return self._commit(body, change)

    def update_seat(
        self, room_id: str, side: str, index: int, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, Seat]:
            room_index, battle = self._battle_at(library, room_id)
            seats = list(_seat_list(battle, side))
            if not 0 <= index < len(seats):
                raise ScriptHttpError(
                    404, {"error": f"{side} 没有第 {index} 个座位（共 {len(seats)} 个）"}
                )
            seat = self._model_entity("seat", body)
            seats[index] = seat
            return (
                _with_room_battle(
                    library, room_index, _with_seats(battle, side, seats)
                ),
                seat,
            )

        return self._commit(body, change)

    def delete_seat(
        self, room_id: str, side: str, index: int, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """删座位（归属）：只摘掉这一座；战斗的座位数不合法由面板立刻标红。"""

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, None]:
            room_index, battle = self._battle_at(library, room_id)
            seats = list(_seat_list(battle, side))
            if not 0 <= index < len(seats):
                raise ScriptHttpError(
                    404, {"error": f"{side} 没有第 {index} 个座位（共 {len(seats)} 个）"}
                )
            seats.pop(index)
            return (
                _with_room_battle(
                    library, room_index, _with_seats(battle, side, seats)
                ),
                None,
            )

        return self._commit(body, change)

    # -- 导入内置 -----------------------------------------------------------

    def import_builtin(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """把**只读**内置库克隆进用户库（票 03 §9）：剧本 id 重写，共享表按 id 并集。

        共享表用并集而不是闭包：内置剧本引用的房间 / 模板 / 事件池要一起可编辑，而多带几条
        没有被引用的共享表只会让编辑器列表长一点，不会让库不合法。同名 id 以**用户库**为准
        （用户改过的东西不会被导入覆盖）。
        """
        wanted = body.get("script_id")
        builtin = load_builtin_script_library()
        if wanted is None:
            incoming = builtin.scripts
        elif not isinstance(wanted, str) or all(
            script.id != wanted for script in builtin.scripts
        ):
            raise ScriptHttpError(
                404, {"error": f"内置库里没有剧本 {wanted!r}"}
            )
        else:
            incoming = tuple(script for script in builtin.scripts if script.id == wanted)

        def change(library: ScriptLibrary) -> tuple[ScriptLibrary, None]:
            taken = {script.id for script in library.scripts}
            added: list[Script] = []
            for script in incoming:
                # 内置库是**另一个文件**：用户库里没有同名剧本时保留原 id（"导入"不是"复制
                # 一份叫 imported 的东西"）；撞了才重写（票面「id 重写」落在冲突这一侧）。
                new_id = (
                    script.id
                    if script.id not in taken
                    else _unique_id(script.id, taken, suffix="-imported")
                )
                taken.add(new_id)
                added.append(script.model_copy(update={"id": new_id}))
            tables: dict[str, tuple[Any, ...]] = {}
            for kind in SHARED_TABLES:
                existing = _table(library, kind)
                seen = {item.id for item in existing}
                tables[kind] = (
                    *existing,
                    *(item for item in _table(builtin, kind) if item.id not in seen),
                )
            changed = library.model_copy(
                update={
                    "scripts": (*library.scripts, *added),
                    **tables,
                }
            )
            imported.extend({"id": script.id, "name": script.name} for script in added)
            return changed, None

        imported: list[dict[str, str]] = []

        with self._lock:
            payload = self._commit(body, change, extra={"imported": imported})
        return payload
